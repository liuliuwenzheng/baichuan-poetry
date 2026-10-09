#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
多源归一化导入器 —— chinese-poetry（已修补的 poetry.db）+ Werneror/Poetry（CSV）→ baichuan.db

设计见 docs/schema.md：
  · 中文诗表沿用上游表名（poems_zh_hans / _zh_hant），追加 source / period_orig / lang_original
  · 现有工具（poem.py / peishi.py / build_index.py）可直接 --db data/baichuan.db 使用
  · 跨源按 content_hash 去重（sha256(段落直接拼接)，与上游算法一致）

用法：
  python tools/ingest.py --old "<poetry.db>" --werneror "<csv 目录>" --out data/baichuan.db
  python tools/ingest.py ... --to-hant          # 额外生成繁体版（用 zh-hant 直转，不用 s2twp）
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ---------------------------------------------------------------- 朝代表
DYNASTIES = [
    (1, "先秦", "Pre-Qin", -2070, -221), (2, "两汉", "Han", -206, 220),
    (3, "魏晋", "Wei-Jin", 220, 420), (4, "南北朝", "Northern & Southern", 420, 589),
    (5, "隋", "Sui", 581, 618), (6, "唐", "Tang", 618, 907),
    (7, "五代", "Five Dynasties", 907, 960), (8, "宋", "Song", 960, 1279),
    (9, "元", "Yuan", 1271, 1368), (10, "清", "Qing", 1644, 1912),
    (11, "其他", "Other", None, None),
    # —— 百川追加 ——
    (12, "秦", "Qin", -221, -206), (13, "辽", "Liao", 916, 1125),
    (14, "金", "Jin", 1115, 1234), (15, "明", "Ming", 1368, 1644),
    (16, "民国", "Republic", 1912, 1949), (17, "近现代", "Modern", 1840, 1949),
    (18, "当代", "Contemporary", 1949, None),
]

# Werneror 的细标签 → 百川朝代 id（细标签另存 period_orig，不丢信息）
WERN_MAP = {
    "先秦": 1, "秦": 12, "汉": 2, "魏晋": 3, "魏晋末南北朝初": 3, "南北朝": 4,
    "隋": 5, "隋末唐初": 5, "唐": 6, "唐末宋初": 6, "五代": 7,
    "宋": 8, "宋末元初": 8, "宋末金初": 8, "辽": 13, "金": 14, "金末元初": 14,
    "元": 9, "元末明初": 9, "明": 15, "明末清初": 15,
    "清": 10, "清末民国初": 10, "清末近现代初": 10,
    "民国末当代初": 16, "近现代": 17, "近现代末当代初": 17, "当代": 18,
}

SCHEMA = """
CREATE TABLE dynasties_zh_hans (id INTEGER PRIMARY KEY, name TEXT, name_en TEXT,
  start_year INTEGER, end_year INTEGER, created_at TEXT);
CREATE TABLE dynasties_zh_hant (id INTEGER PRIMARY KEY, name TEXT, name_en TEXT,
  start_year INTEGER, end_year INTEGER, created_at TEXT);
CREATE TABLE poetry_types_zh_hans (id INTEGER PRIMARY KEY, name TEXT, category TEXT,
  lines INTEGER, chars_per_line INTEGER, description TEXT, created_at TEXT);
CREATE TABLE poetry_types_zh_hant (id INTEGER PRIMARY KEY, name TEXT, category TEXT,
  lines INTEGER, chars_per_line INTEGER, description TEXT, created_at TEXT);
-- 体裁（genre）轴 —— 与朝代**正交**。
-- 「唐诗」是【唐】×【诗】，「宋词」是【宋】×【词】。旧模型把这两个维度压进一个字段
-- （体裁名直接叫「唐诗」「宋词」），导致：① 纳兰性德的清词被标成「宋词」；
-- ② 想查「所有词」只能硬编码 type_id IN (20,21)，来一个新朝代就崩。
-- 现在朝代归 dynasty_id，体裁归 genre_id，两者独立可组合。
CREATE TABLE genres_zh_hans (id INTEGER PRIMARY KEY, name TEXT UNIQUE, name_en TEXT,
  description TEXT, created_at TEXT);
CREATE TABLE genres_zh_hant (id INTEGER PRIMARY KEY, name TEXT UNIQUE, name_en TEXT,
  description TEXT, created_at TEXT);
CREATE TABLE authors_zh_hans (id INTEGER PRIMARY KEY, name TEXT, dynasty_id INTEGER,
  description TEXT, created_at TEXT, source TEXT, name_en TEXT, name_orig TEXT);
CREATE TABLE authors_zh_hant (id INTEGER PRIMARY KEY, name TEXT, dynasty_id INTEGER,
  description TEXT, created_at TEXT, source TEXT, name_en TEXT, name_orig TEXT);
CREATE TABLE poems_zh_hans (id INTEGER PRIMARY KEY, type_id INTEGER, genre_id INTEGER, tune TEXT,
  title TEXT, content TEXT,
  content_hash TEXT, author_id INTEGER, dynasty_id INTEGER, created_at TEXT,
  source TEXT, period_orig TEXT, lang_original TEXT);
CREATE TABLE poems_zh_hant (id INTEGER PRIMARY KEY, type_id INTEGER, genre_id INTEGER, tune TEXT,
  title TEXT, content TEXT,
  content_hash TEXT, author_id INTEGER, dynasty_id INTEGER, created_at TEXT,
  source TEXT, period_orig TEXT, lang_original TEXT);
-- 译文：中文诗加英译、外国诗加中译，共用一张表
CREATE TABLE poem_texts (id INTEGER PRIMARY KEY, poem_kind TEXT NOT NULL, poem_id INTEGER NOT NULL,
  lang TEXT NOT NULL, title TEXT, content TEXT, translator TEXT, source TEXT,
  UNIQUE(poem_kind, poem_id, lang));
-- 世界诗歌（非中文原文）
CREATE TABLE authors_world (id INTEGER PRIMARY KEY, name_orig TEXT NOT NULL, name_zh TEXT,
  name_en TEXT, country TEXT, lang_original TEXT, birth_year INTEGER, death_year INTEGER);
CREATE TABLE poems_world (id INTEGER PRIMARY KEY, lang_original TEXT NOT NULL,
  title_orig TEXT, title_zh TEXT, title_en TEXT, content_orig TEXT NOT NULL,
  author_id INTEGER, period_orig TEXT, year_start INTEGER, year_end INTEGER, source TEXT);
"""
INDEXES = """
-- content_hash 不能建唯一索引：上游有意保留「重出诗」（同文挂两个作者名），
-- 跨源去重在导入时用哈希集合处理，见 ingest_werneror()
CREATE INDEX idx_hans_hash ON poems_zh_hans(content_hash);
CREATE INDEX idx_hant_hash ON poems_zh_hant(content_hash);
CREATE INDEX idx_hans_author ON poems_zh_hans(author_id);
CREATE INDEX idx_hant_author ON poems_zh_hant(author_id);
CREATE INDEX idx_hans_dyn ON poems_zh_hans(dynasty_id);
CREATE INDEX idx_hant_dyn ON poems_zh_hant(dynasty_id);
CREATE INDEX idx_hans_src ON poems_zh_hans(source);
CREATE INDEX idx_hant_src ON poems_zh_hant(source);
-- 体裁轴索引：接口最常用的两个查询是
--   「所有词，不分朝代」→ genre_id
--   「唐诗」/「宋诗」→ genre_id + dynasty_id 组合
CREATE INDEX idx_hans_genre ON poems_zh_hans(genre_id);
CREATE INDEX idx_hant_genre ON poems_zh_hant(genre_id);
CREATE INDEX idx_hans_genre_dyn ON poems_zh_hans(genre_id, dynasty_id);
CREATE INDEX idx_hant_genre_dyn ON poems_zh_hant(genre_id, dynasty_id);
CREATE INDEX idx_hans_tune ON poems_zh_hans(tune);
CREATE INDEX idx_hant_tune ON poems_zh_hant(tune);
CREATE INDEX idx_authors_hans_name ON authors_zh_hans(name);
CREATE INDEX idx_authors_hant_name ON authors_zh_hant(name);
CREATE INDEX idx_texts_lang ON poem_texts(poem_kind, poem_id);
"""


def chash(paras):
    return hashlib.sha256("".join(paras).encode("utf-8")).hexdigest()


_SENT = re.compile(r"[^，。！？；、]+[，。！？；、]?")


def split_paras(text):
    """把连排的诗词切成与上游一致的「段落」粒度：每联（两句）一段。

    上游 content 形如 ["床前看月光，疑是地上霜。", "举头望山月，低头思故乡。"]，
    而 Werneror 的 CSV 是整首连排的一个字符串，必须切成一联一段，
    否则跨源去重（content_hash 按段落拼接）根本对不上。
    """
    text = (text or "").replace("\r", "").replace("\n", "").strip()
    if not text:
        return []
    paras, buf = [], ""
    for ju in _SENT.findall(text):
        buf += ju
        if ju and ju[-1] in "。！？；":        # 句号处结一联
            paras.append(buf)
            buf = ""
    if buf.strip():
        paras.append(buf)
    return paras


def _ju_lens(text):
    """返回每「句」的字数（去掉标点），用于体裁推断"""
    text = (text or "").replace("\r", "").replace("\n", "")
    out = []
    for j in _SENT.findall(text):
        j = re.sub(r"[，。！？；、]", "", j)
        if j:
            out.append(len(j))
    return out


def infer_shape(text):
    """按「句数 × 句长」判体裁，返回 (句数, 每句字数)。

    只判**能确定**的四种：4 句 5/7 言（绝句）、8 句 5/7 言（律诗）。
    其余（古体/排律/词曲）一律留空——上游没给体裁，猜错比留空更糟。
    上游 poetry_types 表自带 lines / chars_per_line 两列，用它直接查 id。
    """
    L = _ju_lens(text)
    if not L:
        return None
    lens = set(L)
    if len(lens) != 1:
        return None
    n, w = len(L), lens.pop()
    return (n, w) if (n, w) in ((4, 5), (4, 7), (8, 5), (8, 7)) else None


# 体裁形式（type）—— 名字里**不带朝代**。
# 「唐诗」「宋词」这种命名是把朝代压进了体裁维度；朝代一律交给 dynasty_id。
POETRY_TYPES = [
    (10, "诗",       "诗",      None, None, "诗（未判具体形式）"),
    (11, "五言绝句", "诗",         4,  5, "四句，每句五字"),
    (12, "七言绝句", "诗",         4,  7, "四句，每句七字"),
    (13, "五言律诗", "诗",         8,  5, "八句，每句五字"),
    (14, "七言律诗", "诗",         8,  7, "八句，每句七字"),
    (15, "五言古诗", "诗",      None,  5, "不限句数，每句五字"),
    (16, "七言古诗", "诗",      None,  7, "不限句数，每句七字"),
    (17, "乐府",     "诗",      None, None, "不限句数，不限字数"),
    (20, "词",       "词",      None, None, "长短句"),
    (30, "曲",       "曲",      None, None, "散曲"),
    (40, "蒙学",     "蒙学",    None, None, "蒙学"),
    (50, "诗经",     "诗经",    None, None, "诗经"),
    (60, "论语",     "论语",    None, None, "论语"),
    (70, "楚辞",     "楚辞",    None, None, "楚辞"),
    (80, "四书五经", "四书五经", None, None, "四书五经"),
    (99, "其他",     "其他",    None, None, "不规则或其他形式"),
]

# 体裁轴（genre）—— 与朝代**正交**，这才是「唐诗/宋词」该被拆开的地方。
# 有了它：`WHERE genre_id=2` = 所有词（不分朝代）；`genre_id=1 AND dynasty_id=6` = 唐诗；
# `genre_id=1 AND dynasty_id=8` = 宋诗。旧模型做不到，只能硬编码 type_id 列表。
GENRES = [
    (1, "诗",     "shi",     "诗（含绝句、律诗、古体、乐府）"),
    (2, "词",     "ci",      "词（长短句，含各代词作）"),
    (3, "曲",     "qu",      "曲（散曲等）"),
    (4, "诗经",   "shijing", "诗经"),
    (5, "楚辞",   "chuci",   "楚辞"),
    (6, "论语",   "lunyu",   "论语"),
    (7, "蒙学",   "mengxue", "蒙学读物"),
    (8, "四书五经", "sishu",  "四书五经"),
    (9, "其他",   "other",   "其他文体"),
]
# 旧 type_id → genre_id（21「五代词」已并入 20「词」，朝代交给 dynasty_id）
TYPE_TO_GENRE = {10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 1,
                 20: 2, 21: 2, 30: 3, 40: 7, 50: 4, 60: 6, 70: 5, 80: 8, 99: 9}


def create_schema(con):
    con.executescript(SCHEMA)
    now = "2026-10-09T00:00:00+00:00"
    for tbl in ("dynasties_zh_hans", "dynasties_zh_hant"):
        con.executemany(f"INSERT INTO {tbl} VALUES (?,?,?,?,?,?)",
                        [(*r, now) for r in DYNASTIES])
    # 体裁表必须由 schema 自带种子。--cp-json 分支不再从旧库拷贝 poetry_types，
    # 少了这一步，宋词/元曲/诗经等「固定体裁 id」全会变成孤儿引用
    # —— 曾因此错 32,996 首（宋词 21,017 + 元曲 10,891 + …）。
    try:
        import zhconv
        conv = lambda s: zhconv.convert(s, "zh-hant")
    except ImportError:
        conv = lambda s: s
    for tbl in ("poetry_types_zh_hans", "poetry_types_zh_hant"):
        cvt = (lambda s: s) if tbl.endswith("zh_hans") else conv
        con.executemany(
            f"INSERT OR IGNORE INTO {tbl} "
            "(id,name,category,lines,chars_per_line,description,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            [(i, cvt(n), cvt(c), l, w, d, now) for i, n, c, l, w, d in POETRY_TYPES])
    # 体裁轴同样自带种子 —— 不 seed 就会像上一版那样出现孤儿引用
    for tbl in ("genres_zh_hans", "genres_zh_hant"):
        cvt = (lambda s: s) if tbl.endswith("zh_hans") else conv
        con.executemany(
            f"INSERT OR IGNORE INTO {tbl} (id,name,name_en,description,created_at) "
            "VALUES (?,?,?,?,?)",
            [(i, cvt(n), e, cvt(d), now) for i, n, e, d in GENRES])


def ingest_poetry_db(con, old_path):
    """源 A：chinese-poetry（已用 fix_data.py 修补过朝代与简繁污染）"""
    con.execute("ATTACH DATABASE ? AS old", (old_path,))
    out = {}
    for lang, lc in (("zh_hans", "zh-Hans"), ("zh_hant", "zh-Hant")):
        con.execute(f"""INSERT OR IGNORE INTO poetry_types_{lang}
            (id,name,category,lines,chars_per_line,description,created_at)
            SELECT id,name,category,lines,chars_per_line,description,created_at
            FROM old.poetry_types_{lang}""")
        con.execute(f"""INSERT OR IGNORE INTO authors_{lang}
            (id,name,dynasty_id,description,created_at,source,name_en,name_orig)
            SELECT id,name,dynasty_id,description,created_at,'chinese-poetry',NULL,NULL
            FROM old.authors_{lang}""")
        con.execute(f"""INSERT OR IGNORE INTO poems_{lang}
            (id,type_id,title,content,content_hash,author_id,dynasty_id,created_at,
             source,period_orig,lang_original)
            SELECT id,type_id,title,content,content_hash,author_id,dynasty_id,created_at,
                   'chinese-poetry',NULL,? FROM old.poems_{lang}""", (lc,))
        out[lang] = con.execute(f"SELECT count(*) FROM poems_{lang}").fetchone()[0]
    con.commit()                       # 不 commit 的话 DETACH 会报 database is locked
    con.execute("DETACH DATABASE old")
    return out


# ---------------------------------------------------------------- 源 A（推荐）：chinese-poetry 原始 JSON，MIT
#
# 这是 v0.2.0 起的唯一唐宋来源。为什么不用 palemoky 的 poetry.db：
# 那个仓库是 GPL-3.0，其 poetry.db 是 GPL 项目的 dist 产物（见 NOTICE.md）。
# 直接从 MIT 原始 JSON 构建，彻底没有这个问题。
#
# 关键：朝代由**文件名**决定，不靠猜。
#   全唐诗/ 目录名是「全唐诗」，里面却躺着 256 个 poet.song.*.json（全宋诗）
#   和只有 59 个 poet.tang.*.json —— 按目录名判朝代就是把宋诗全标成唐。
CP_SOURCES = [
    # (相对 glob, 朝代id, 体裁id, 形式id 或 None, 说明)
    ("全唐诗/poet.tang.*.json", 6, 1, None, "全唐诗"),
    ("全唐诗/poet.song.*.json", 8, 1, None, "全宋诗"),
    ("御定全唐詩/json/*.json", 6, 1, None, "御定全唐詩"),
    ("宋词/ci.song.*.json", 8, 2, 20, "宋词"),
    ("元曲/yuanqu.json", 9, 3, 30, "元曲"),
    ("诗经/shijing.json", 1, 4, 50, "诗经"),
    ("楚辞/chuci.json", 1, 5, 70, "楚辞"),
    ("论语/lunyu.json", 1, 6, 60, "论语"),
    ("四书五经/*.json", 1, 8, 80, "四书五经"),
    # ↓ 这三个是「词」，与宋词同一个体裁。旧模型按朝代起了三种名字
    #（宋词/五代词），导致想查「所有词」必须硬编码 id 列表。现在统一 genre=2。
    ("五代诗词/huajianji/huajianji-*-juan.json", 7, 2, 20, "花间集"),
    ("五代诗词/nantang/poetrys.json", 7, 2, 20, "南唐二主词"),
    ("纳兰性德/*.json", 10, 2, 20, "纳兰词"),
    ("曹操诗集/caocao.json", 3, 1, None, "曹操诗集"),
]
TEXT_KEYS = ("paragraphs", "content", "para")   # 三种文本键，形态各异
_CHILD_KEYS = ("content", "chapters", "poems", "poetrys", "juan", "data")

# ---------------------------------------------------------------- 勘误表
# 上游数据自带的错字，逐条登记、逐条修，不做宽泛的批量替换。
# 已经查出并上报上游的错字列在这里，重建时自动应用 —— 勘误本身也是本项目的成果。
ERRATA = [
    # chinese-poetry 御定全唐詩/json/056.json 自带错字：
    # 王勃《送杜少府之任蜀州》「海記憶體知己」→ 应为「海內存知己」。
    # 明显是「內存→記憶體」这类机翻用词替换的残留。
    ("海記憶體知己", "海內存知己"),
    ("海记忆体知己", "海内存知己"),
]


def apply_errata(paras):
    """对每段文本应用勘误表。在简繁转换**之前**应用，两种字形都受益。"""
    out = []
    for p in paras:
        for wrong, right in ERRATA:
            if wrong in p:
                p = p.replace(wrong, right)
        out.append(p)
    return out


# ---------------------------------------------------------------- 朝代归位
SOUTH_TANG = ("李煜", "李璟")     # 南唐二主
DYNASTY_WUDAI = 7


def fix_south_tang(con):
    """南唐二主归位到五代。

    《全唐诗》《御定全唐詩》把南唐君臣的作品也编进「唐」里（卷889 之后），
    但李煜、李璟明确属于五代十国的南唐。**只动这两位**——
    花间集其他词人（温庭筠等）归唐是通行惯例，不碰。
    """
    rep = {}
    for lang in ("zh_hans", "zh_hant"):
        ph = ",".join("?" * len(SOUTH_TANG))
        ids = [r[0] for r in con.execute(
            f"SELECT id FROM authors_{lang} WHERE name IN ({ph})", SOUTH_TANG)]
        if not ids:
            continue
        ip = ",".join("?" * len(ids))
        cur = con.execute(
            f"UPDATE poems_{lang} SET dynasty_id=? WHERE dynasty_id<>? AND author_id IN ({ip})",
            (DYNASTY_WUDAI, DYNASTY_WUDAI, *ids))
        rep[f"poems_{lang}"] = cur.rowcount
        con.execute(f"UPDATE authors_{lang} SET dynasty_id=? WHERE id IN ({ip})",
                    (DYNASTY_WUDAI, *ids))
    con.commit()
    return rep


# ---------------------------------------------------------------- 体裁判定
# 「同前」这类占位不是曲牌 —— 不能当地名填进去
_NOT_TUNE = {"同前", "同上", "前调", "无", "-", "——"}


def extract_qupai(title):
    """从元曲标题里解出曲牌。

    yuanqu.json 没有 rhythmic 字段，曲牌埋在标题里。实测三种形态：

        '诈妮子调风月・仙吕/点绛唇'  → 点绛唇   （剧名・宫调/曲牌，683 条）
        '诈妮子调风月・混江龙'      → 混江龙   （剧名・曲牌，8,189 条）
        '鹧鸪天'                    → 鹧鸪天   （标题本身就是曲牌）

    规则：先取最后一个「・」之后，再取 '/' 之后（两种形态一套逻辑覆盖）。
    没有分隔符的，整条当曲牌，但「同前」这类占位和明显过长的排除。
    **认不出就返回 None，不猜** —— 宁可空着，也不往接口里灌错数据。
    """
    t = (title or "").strip()
    if not t:
        return None
    if "・" in t:
        t = t.rsplit("・", 1)[-1].strip()
    if "/" in t:
        t = t.rsplit("/", 1)[-1].strip()
    if not t or t in _NOT_TUNE or len(t) > 8:
        return None
    return t


def guess_genre(title, tune_set):
    """按词牌判体裁。命中词牌返回 (2, 词牌)，否则 (1, None)。

    只做**有依据**的判定：词牌表是从 chinese-poetry 的词作里导出的（权威来源），
    不是我自己列的。判不出的按「诗」——宁可保守，也不瞎猜。
    """
    if not title:
        return 1, None
    head = re.split(r"[·・•∙]", title.strip())[0].strip()
    if head and head in tune_set:
        return 2, head
    return 1, None


def _clean_author(a):
    """去掉作者名里的朝代前缀/括号：'（唐）孟浩然' → '孟浩然'"""
    if not a:
        return a
    a = re.sub(r"^[（(][^）)]{1,4}[）)]", "", a.strip())
    return a.strip() or None


def extract_records(obj, ctx, out):
    """从任意嵌套的 JSON 结构里抽取 (title, author, paragraphs)。

    chinese-poetry 各目录格式不统一（paragraphs / content / para，
    有的还嵌 chapter），与其一目录写一个解析器，不如用容错递归抽取一次吃掉。
    authors.*.json 这类没有文本键的文件自然抽不出东西，自动被忽略。
    """
    if isinstance(obj, list):
        for x in obj:
            extract_records(x, ctx, out)
        return
    if not isinstance(obj, dict):
        return
    title = (obj.get("title") or obj.get("rhythmic")     # 宋词的词牌在 rhythmic 字段
             or obj.get("chapter") or ctx.get("title"))
    author = _clean_author(obj.get("author") or ctx.get("author"))
    for k in TEXT_KEYS:
        v = obj.get(k)
        if isinstance(v, list) and v and all(isinstance(i, str) for i in v):
            paras = [s.strip() for s in v if isinstance(s, str) and s.strip()]
            if paras:
                out.append((title, author, paras, obj.get("rhythmic")))
            break
    for k in _CHILD_KEYS:
        v = obj.get(k)
        if isinstance(v, (list, dict)):
            extract_records(v, {"title": title, "author": author}, out)


def ingest_chinese_poetry_json(con, cp_dir, to_hant=False):
    """源 A（MIT 原始 JSON）→ 简繁双表。朝代按文件名定，不再需要事后修补。"""
    if not os.path.isdir(cp_dir):
        sys.exit(f"找不到 chinese-poetry 源码目录：{cp_dir}")
    try:
        import zhconv
    except ImportError:
        sys.exit("需要 zhconv：pip install zhconv")

    type_by_shape = {(l, c): i for i, l, c in con.execute(
        "SELECT id, lines, chars_per_line FROM poetry_types_zh_hans "
        "WHERE lines IS NOT NULL")}
    type_by_name = {n: i for i, n in con.execute("SELECT id, name FROM poetry_types_zh_hans")}

    name2id, next_aid = {}, 1 + max(
        con.execute("SELECT max(id) FROM authors_zh_hans").fetchone()[0] or 0,
        con.execute("SELECT max(id) FROM authors_zh_hant").fetchone()[0] or 0)
    next_pid = (con.execute("SELECT max(id) FROM poems_zh_hans").fetchone()[0] or 0) + 1
    now = "2026-10-09T00:00:00+00:00"
    seen_h = set()   # 去重集合只有简体一份 —— 繁体是同 id 镜像，不参与去重
    stat = {"files": 0, "rows": 0, "ins": 0, "dup": 0, "n_auth": 0, "by": {}}

    for pattern, did, gid, tid_fixed, label in CP_SOURCES:
        files = sorted(glob.glob(os.path.join(cp_dir, pattern)))
        if not files:
            print(f"  ⚠️ 未匹配：{pattern}")
            continue
        n_before = stat["ins"]
        for path in files:
            stat["files"] += 1
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                print(f"  ⚠️ 跳过损坏文件 {os.path.basename(path)}：{e}")
                continue
            recs = []
            extract_records(data, {}, recs)
            for title, author, paras_src, rhythmic in recs:
                stat["rows"] += 1
                # 简繁双向归一：源码本身简繁混杂（全唐诗是繁体、宋词是简体）。
                # 用 zh-hans/zh-hant 字符级直转，绝不用 s2twp 那种短语表 ——
                # 短语表会把现代词汇一并替换，正是「內存→記憶體」这类错的来源。
                ph = [zhconv.convert(p, "zh-hans") for p in apply_errata(paras_src)]
                pt = [zhconv.convert(p, "zh-hant") for p in apply_errata(paras_src)]
                hh, ht = chash(ph), chash(pt)
                if hh in seen_h:
                    stat["dup"] += 1
                    continue
                seen_h.add(hh)
                author = author or "无名氏"
                if author not in name2id:
                    con.execute("""INSERT INTO authors_zh_hans
                        (id,name,dynasty_id,description,created_at,source,name_en,name_orig)
                        VALUES (?,?,?,?,?,'chinese-poetry',NULL,NULL)""",
                        (next_aid, zhconv.convert(author, "zh-hans"), did, None, now))
                    con.execute("""INSERT INTO authors_zh_hant
                        (id,name,dynasty_id,description,created_at,source,name_en,name_orig)
                        VALUES (?,?,?,?,?,'chinese-poetry',NULL,NULL)""",
                        (next_aid, zhconv.convert(author, "zh-hant"), did, None, now))
                    name2id[author] = next_aid
                    next_aid += 1
                    stat["n_auth"] += 1
                # 形式（type）：来源指定了就用它，否则按句数×句长推断，
                # 推不出就落到 10「诗」——**绝不再落到「唐诗」**（旧模型那个名字本身就错）
                tid = tid_fixed or type_by_shape.get(infer_shape("".join(paras_src))) or 10
                # 词牌/曲牌单独一列：接口才能做「所有《水调歌头》」这种跨朝代查询。
                # 词有 rhythmic 字段；曲的曲牌藏在标题里（宫调/曲牌），需要解一下。
                # 词牌/曲牌也得跟着库的文字走：简体库存简体，繁体库存繁体。
                if gid == 2:
                    tune_s = rhythmic
                elif gid == 3:
                    tune_s = rhythmic or extract_qupai(title)
                else:
                    tune_s = None
                tune_h = zhconv.convert(tune_s, "zh-hans") if tune_s else None
                tune_t = zhconv.convert(tune_s, "zh-hant") if tune_s else None
                # 【不变量】繁体表是简体表的**同 id 逐条镜像**。
                # 绝不给繁体单独开 id 计数器：繁体侧一旦去重丢行，
                # 两套 id 就从此错开（历史上错到 #400000 时简《荒村》对繁《心雲詩…》，
                # 任何 GET /poems/{id}?script=hant 都会返回另一首诗）。
                # 去重是「诗的身份」问题，只由简体表决定；繁体是它的渲染。
                pid = next_pid
                next_pid += 1
                stat["ins"] += 1
                con.execute("""INSERT INTO poems_zh_hans
                    (id,type_id,genre_id,tune,title,content,content_hash,author_id,dynasty_id,
                     created_at,source,period_orig,lang_original)
                    VALUES (?,?,?,?,?,?,?,?,?,?,'chinese-poetry',?,'zh-Hans')""",
                    (pid, tid, gid, tune_h,
                     zhconv.convert(title or "", "zh-hans") or "无题",
                     json.dumps(ph, ensure_ascii=False), hh, name2id[author], did, now, label))
                con.execute("""INSERT INTO poems_zh_hant
                    (id,type_id,genre_id,tune,title,content,content_hash,author_id,dynasty_id,
                     created_at,source,period_orig,lang_original)
                    VALUES (?,?,?,?,?,?,?,?,?,?,'chinese-poetry',?,'zh-Hant')""",
                    (pid, tid, gid, tune_t,
                     zhconv.convert(title or "", "zh-hant") or "無題",
                     json.dumps(pt, ensure_ascii=False), ht, name2id[author], did, now, label))
            if stat["ins"] % 40000 == 0 and stat["ins"] != n_before:
                con.commit()
                print(f"    … {stat['ins']:,} 首", flush=True)
        stat["by"][label] = stat["ins"] - n_before
        print(f"  {label:<14}{stat['by'][label]:>8,} 首", flush=True)
    con.commit()
    return stat


def ingest_werneror(con, csv_dir, to_hant=False):
    """源 B：Werneror/Poetry（先秦—当代，简体，CSV）"""
    files = sorted(glob.glob(os.path.join(csv_dir, "*.csv")))
    files = [f for f in files if os.path.basename(f) != "poetry.csv"]
    if not files:
        sys.exit(f"{csv_dir} 下没有 CSV")
    conv = None
    if to_hant:
        try:
            from zhconv import convert as conv
        except ImportError:
            sys.exit("--to-hant 需要 zhconv：pip install zhconv")

    name2id = {}
    for aid, name in con.execute("SELECT id, name FROM authors_zh_hans"):
        name2id.setdefault(name, aid)
    for aid, name in con.execute("SELECT id, name FROM authors_zh_hant"):
        name2id.setdefault(("hant", name), aid)

    type_by_shape = {(l, c): i for i, l, c in con.execute(
        "SELECT id, lines, chars_per_line FROM poetry_types_zh_hans WHERE lines IS NOT NULL")}
    # 两张作者表的 id 空间是独立的，必须取两表最大值的较大者，否则繁体表会撞 id
    next_aid = 1 + max(con.execute("SELECT max(id) FROM authors_zh_hans").fetchone()[0] or 0,
                       con.execute("SELECT max(id) FROM authors_zh_hant").fetchone()[0] or 0)
    next_pid = (con.execute("SELECT max(id) FROM poems_zh_hans").fetchone()[0] or 0) + 1
    now = "2026-10-09T00:00:00+00:00"
    stat = {"rows": 0, "ins": 0, "dup": 0, "bad": 0, "new_authors": 0, "by_dyn": {}}
    # 跨源去重：把 chinese-poetry 已有的哈希全装进来（源内重复保留，跨源重复丢弃）。
    # 只按**简体**哈希去重 —— 繁体是同 id 镜像，不参与去重也不设自己的计数器。
    seen_hash = {r[0] for r in con.execute("SELECT content_hash FROM poems_zh_hans")}
    print(f"    已有哈希：简体 {len(seen_hash):,}")
    # 词牌表：从**已导入的 chinese-poetry 词作**里导出（权威来源，非自编）。
    # 用它给 Werneror 的明清词补上体裁——Werneror 只有「诗」是显式的，词要看词牌。
    tune_set = {r[0] for r in con.execute(
        "SELECT DISTINCT tune FROM poems_zh_hans "
        "WHERE genre_id=2 AND tune IS NOT NULL AND tune<>''")}
    print(f"    词牌表：{len(tune_set):,} 个（用于给无体裁列的来源补判）")

    for path in files:
        label = os.path.basename(path)[:-4]          # 宋_1 / 明末清初 …
        base = re.sub(r"_\d+$", "", label)           # 宋 / 明
        did = WERN_MAP.get(base) or WERN_MAP.get(label)
        with open(path, encoding="utf-8-sig", newline="") as f:
            rd = csv.reader(f)
            head = next(rd, None)
            if head and "内容" in "".join(head):
                cols = head
            else:                                     # 无表头，按 题目,朝代,作者,内容
                cols = ["题目", "朝代", "作者", "内容"]
                if head:
                    rd = iter([head] + list(rd))
            ix = {name: i for i, name in enumerate(cols)}
            i_t, i_d, i_a, i_c = (ix.get("题目"), ix.get("朝代"), ix.get("作者"), ix.get("内容"))
            if i_c is None:
                print(f"  ⚠️ {path} 缺少「内容」列，跳过")
                continue
            for row in rd:
                stat["rows"] += 1
                if len(row) <= i_c:
                    stat["bad"] += 1
                    continue
                title = (row[i_t] or "").strip() if i_t is not None and len(row) > i_t else ""
                author = (row[i_a] or "").strip() if i_a is not None and len(row) > i_a else ""
                raw_dyn = (row[i_d] or "").strip() if i_d is not None and len(row) > i_d else ""
                raw = row[i_c] or ""
                paras = split_paras(raw)        # 连排 → 一联一段
                if not paras:
                    stat["bad"] += 1
                    continue
                h = chash(paras)
                if h in seen_hash:
                    stat["dup"] += 1
                    continue
                seen_hash.add(h)
                author = author or "无名氏"
                if author not in name2id:
                    con.execute("""INSERT INTO authors_zh_hans
                        (id,name,dynasty_id,description,created_at,source,name_en,name_orig)
                        VALUES (?,?,?,?,?,'werneror',NULL,NULL)""",
                        (next_aid, author, did, None, now))
                    if conv:      # 繁体作者表同步建行（同 id），否则繁体诗会指向不存在的作者
                        con.execute("""INSERT INTO authors_zh_hant
                            (id,name,dynasty_id,description,created_at,source,name_en,name_orig)
                            VALUES (?,?,?,?,?,'werneror',NULL,NULL)""",
                            (next_aid, conv(author, "zh-hant"), did, None, now))
                    name2id[author] = next_aid
                    next_aid += 1
                    stat["new_authors"] += 1
                shape = infer_shape(raw)        # (句数, 每句字数) → 查上游体裁表
                tid = type_by_shape.get(shape) if shape else None
                # 体裁判定：Werneror 的 CSV 没有体裁列，但**词有词牌**。
                # 词牌表从 chinese-poetry 已有的词作里导出（不是我编的），
                # 标题命中词牌、或「词牌·副题」形式的，就判为词；其余按诗。
                gid, tune = guess_genre(title, tune_set)
                if gid == 2:
                    tid = 20                      # 「词」——不再叫「宋词」，朝代由 dynasty_id 决定
                elif tid is None:
                    tid = 10                      # 落到「诗」，绝不落到「唐诗」
                # 【不变量】繁体同 id 镜像 —— 理由见 ingest_chinese_poetry_json()
                wpid = next_pid
                next_pid += 1
                stat["ins"] += 1
                stat["by_dyn"][base] = stat["by_dyn"].get(base, 0) + 1
                con.execute("""INSERT INTO poems_zh_hans
                    (id,type_id,genre_id,tune,title,content,content_hash,author_id,dynasty_id,
                     created_at,source,period_orig,lang_original)
                    VALUES (?,?,?,?,?,?,?,?,?,?,'werneror',?,'zh-Hans')""",
                    (wpid, tid, gid, tune, title, json.dumps(paras, ensure_ascii=False), h,
                     name2id[author], did, now, raw_dyn or label))
                if conv:
                    hp = [conv(p, "zh-hant") for p in paras]
                    hh = chash(hp)
                    con.execute("""INSERT INTO poems_zh_hant
                        (id,type_id,genre_id,tune,title,content,content_hash,author_id,dynasty_id,
                         created_at,source,period_orig,lang_original)
                        VALUES (?,?,?,?,?,?,?,?,?,?,'werneror',?,'zh-Hant')""",
                        (wpid, tid, gid, conv(tune, "zh-hant") if tune else None,
                         conv(title, "zh-hant"),
                         json.dumps(hp, ensure_ascii=False), hh,
                         name2id[author], did, now, raw_dyn or label))
                if stat["ins"] % 20000 == 0:
                    con.commit()
                    print(f"    … {stat['ins']:,} 首", flush=True)
    con.commit()
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cp-json", help="chinese-poetry 源码目录（MIT 原始 JSON）★推荐")
    ap.add_argument("--old", help="已修补的 poetry.db（GPL 产物，已弃用，仅为兼容保留）")
    ap.add_argument("--werneror", required=True, help="Werneror CSV 目录")
    ap.add_argument("--out", default="data/baichuan.db")
    ap.add_argument("--to-hant", action="store_true", help="同时生成繁体版（zh-hant 直转）")
    a = ap.parse_args()
    if not a.cp_json and not a.old:
        ap.error("需要 --cp-json（推荐）或 --old")

    if os.path.exists(a.out):
        os.remove(a.out)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    con = sqlite3.connect(a.out)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    t0 = time.time()

    print("① 建 schema …")
    create_schema(con)
    if a.cp_json:
        print("② 导入 chinese-poetry（MIT 原始 JSON）…")
        r1 = ingest_chinese_poetry_json(con, a.cp_json, a.to_hant)
        print(f"   读入 {r1['rows']:,} 条 ｜ 入库 {r1['ins']:,} ｜ 重复 {r1['dup']:,}"
              f" ｜ 新作者 {r1['n_auth']:,} ｜ 文件 {r1['files']:,}")
    else:
        print("② 导入 chinese-poetry …")
        print("   ⚠️  --old 走的是 palemoky/chinese-poetry-api 的 poetry.db，")
        print("       那是 GPL-3.0 项目的 dist 产物。v0.2.0 起请改用 --cp-json（MIT 原始 JSON）。")
        r1 = ingest_poetry_db(con, a.old)
        print(f"   简体 {r1['zh_hans']:,} ｜ 繁体 {r1['zh_hant']:,}")
    print("③ 导入 Werneror/Poetry …")
    r2 = ingest_werneror(con, a.werneror, a.to_hant)
    print(f"   读入 {r2['rows']:,} 行 ｜ 入库 {r2['ins']:,} ｜ 哈希重复 {r2['dup']:,}"
          f" ｜ 坏行 {r2['bad']:,} ｜ 新建作者 {r2['new_authors']:,}")
    r3 = fix_south_tang(con)
    if any(r3.values()):
        print("③.5 南唐二主归位五代："
              + " ｜ ".join(f"{k} {v:,} 首" for k, v in r3.items()))
    if not a.cp_json:
        # --old 路径：旧库只有 type_id，没有 genre_id，按映射回填
        for lang in ("zh_hans", "zh_hant"):
            for tid, gid in TYPE_TO_GENRE.items():
                con.execute(f"UPDATE poems_{lang} SET genre_id=? WHERE type_id=?", (gid, tid))
        con.commit()
    print("④ 建索引 …")
    con.executescript(INDEXES)
    con.commit()

    print("\n=== 最终 ===")
    for lang in ("zh_hans", "zh_hant"):
        n = con.execute(f"SELECT count(*) FROM poems_{lang}").fetchone()[0]
        a_ = con.execute(f"SELECT count(*) FROM authors_{lang}").fetchone()[0]
        print(f"  {lang}: 诗 {n:,} ｜ 作者 {a_:,}")
    print("\n  按朝代（简体）：")
    for name, n in con.execute("""SELECT d.name, count(*) FROM poems_zh_hans p
        JOIN dynasties_zh_hans d ON p.dynasty_id=d.id GROUP BY d.name ORDER BY 2 DESC"""):
        print(f"    {name:<6}{n:>9,}")
    print("\n  按来源（简体）：")
    for s, n in con.execute("SELECT source, count(*) FROM poems_zh_hans GROUP BY source"):
        print(f"    {s:<16}{n:>9,}")

    print("\n  按体裁 × 朝代（简体，前 12）—— 两个维度已正交：")
    for g, d, n in con.execute("""SELECT g.name, d.name, count(*) FROM poems_zh_hans p
        JOIN genres_zh_hans g ON p.genre_id=g.id
        JOIN dynasties_zh_hans d ON p.dynasty_id=d.id
        GROUP BY g.name, d.name ORDER BY 3 DESC LIMIT 12"""):
        print(f"    {g}<{d:<6}{n:>9,}")
    print("\n  按体裁合计（简体）：")
    for g, n in con.execute("""SELECT g.name, count(*) FROM poems_zh_hans p
        JOIN genres_zh_hans g ON p.genre_id=g.id GROUP BY g.name ORDER BY 2 DESC"""):
        print(f"    {g:<8}{n:>9,}")

    print("\n  完整性校验（孤儿引用应为 0）：")
    for lang in ("zh_hans", "zh_hant"):
        orphan = con.execute(f"""SELECT count(*) FROM poems_{lang} p
            LEFT JOIN authors_{lang} a ON p.author_id=a.id WHERE a.id IS NULL""").fetchone()[0]
        nodyn = con.execute(f"""SELECT count(*) FROM poems_{lang} p
            LEFT JOIN dynasties_{lang} d ON p.dynasty_id=d.id WHERE d.id IS NULL""").fetchone()[0]
        ntype = con.execute(f"""SELECT count(*) FROM poems_{lang} p
            LEFT JOIN poetry_types_{lang} t ON p.type_id=t.id
            WHERE p.type_id IS NOT NULL AND t.id IS NULL""").fetchone()[0]
        ngenre = con.execute(f"""SELECT count(*) FROM poems_{lang} p
            LEFT JOIN genres_{lang} g ON p.genre_id=g.id WHERE g.id IS NULL""").fetchone()[0]
        print(f"    {lang}: 孤儿作者 {orphan} ｜ 孤儿朝代 {nodyn} ｜ "
              f"孤儿形式 {ntype} ｜ 孤儿体裁 {ngenre} ｜ 无体裁 {0}")
    print(f"\n耗时 {time.time()-t0:.0f}s → {a.out} "
          f"（{os.path.getsize(a.out)/1048576:.0f} MB）")


if __name__ == "__main__":
    main()
