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
CREATE TABLE authors_zh_hans (id INTEGER PRIMARY KEY, name TEXT, dynasty_id INTEGER,
  description TEXT, created_at TEXT, source TEXT, name_en TEXT, name_orig TEXT);
CREATE TABLE authors_zh_hant (id INTEGER PRIMARY KEY, name TEXT, dynasty_id INTEGER,
  description TEXT, created_at TEXT, source TEXT, name_en TEXT, name_orig TEXT);
CREATE TABLE poems_zh_hans (id INTEGER PRIMARY KEY, type_id INTEGER, title TEXT, content TEXT,
  content_hash TEXT, author_id INTEGER, dynasty_id INTEGER, created_at TEXT,
  source TEXT, period_orig TEXT, lang_original TEXT);
CREATE TABLE poems_zh_hant (id INTEGER PRIMARY KEY, type_id INTEGER, title TEXT, content TEXT,
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


def create_schema(con):
    con.executescript(SCHEMA)
    now = "2026-10-09T00:00:00+00:00"
    for tbl in ("dynasties_zh_hans", "dynasties_zh_hant"):
        con.executemany(f"INSERT INTO {tbl} VALUES (?,?,?,?,?,?)",
                        [(*r, now) for r in DYNASTIES])


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
    next_pid_h = (con.execute("SELECT max(id) FROM poems_zh_hant").fetchone()[0] or 0) + 1
    now = "2026-10-09T00:00:00+00:00"
    stat = {"rows": 0, "ins": 0, "dup": 0, "bad": 0, "new_authors": 0, "by_dyn": {}}
    # 跨源去重：把 chinese-poetry 已有的哈希全装进来（源内重复保留，跨源重复丢弃）
    seen_hash = {r[0] for r in con.execute("SELECT content_hash FROM poems_zh_hans")}
    seen_hant = ({r[0] for r in con.execute("SELECT content_hash FROM poems_zh_hant")}
                 if to_hant else set())
    print(f"    已有哈希：简体 {len(seen_hash):,} ｜ 繁体 {len(seen_hant):,}")

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
                con.execute("""INSERT INTO poems_zh_hans
                    (id,type_id,title,content,content_hash,author_id,dynasty_id,created_at,
                     source,period_orig,lang_original)
                    VALUES (?,?,?,?,?,?,?,?,'werneror',?,'zh-Hans')""",
                    (next_pid, tid, title, json.dumps(paras, ensure_ascii=False), h,
                     name2id[author], did, now, raw_dyn or label))
                next_pid += 1
                stat["ins"] += 1
                stat["by_dyn"][base] = stat["by_dyn"].get(base, 0) + 1
                if conv:
                    hp = [conv(p, "zh-hant") for p in paras]
                    hh = chash(hp)
                    if hh not in seen_hant:
                        seen_hant.add(hh)
                        con.execute("""INSERT OR IGNORE INTO poems_zh_hant
                            (id,type_id,title,content,content_hash,author_id,dynasty_id,created_at,
                             source,period_orig,lang_original)
                            VALUES (?,?,?,?,?,?,?,?,'werneror',?,'zh-Hant')""",
                            (next_pid_h, tid, conv(title, "zh-hant"),
                             json.dumps(hp, ensure_ascii=False), hh,
                             name2id[author], did, now, raw_dyn or label))
                        next_pid_h += 1
                if stat["ins"] % 20000 == 0:
                    con.commit()
                    print(f"    … {stat['ins']:,} 首", flush=True)
    con.commit()
    return stat


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True, help="已修补的 poetry.db")
    ap.add_argument("--werneror", required=True, help="Werneror CSV 目录")
    ap.add_argument("--out", default="data/baichuan.db")
    ap.add_argument("--to-hant", action="store_true", help="同时生成繁体版（zh-hant 直转）")
    a = ap.parse_args()

    if os.path.exists(a.out):
        os.remove(a.out)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    con = sqlite3.connect(a.out)
    con.execute("PRAGMA journal_mode=OFF")
    con.execute("PRAGMA synchronous=OFF")
    t0 = time.time()

    print("① 建 schema …")
    create_schema(con)
    print("② 导入 chinese-poetry …")
    r1 = ingest_poetry_db(con, a.old)
    print(f"   简体 {r1['zh_hans']:,} ｜ 繁体 {r1['zh_hant']:,}")
    print("③ 导入 Werneror/Poetry …")
    r2 = ingest_werneror(con, a.werneror, a.to_hant)
    print(f"   读入 {r2['rows']:,} 行 ｜ 入库 {r2['ins']:,} ｜ 哈希重复 {r2['dup']:,}"
          f" ｜ 坏行 {r2['bad']:,} ｜ 新建作者 {r2['new_authors']:,}")
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

    print("\n  完整性校验（孤儿引用应为 0）：")
    for lang in ("zh_hans", "zh_hant"):
        orphan = con.execute(f"""SELECT count(*) FROM poems_{lang} p
            LEFT JOIN authors_{lang} a ON p.author_id=a.id WHERE a.id IS NULL""").fetchone()[0]
        nodyn = con.execute(f"""SELECT count(*) FROM poems_{lang} p
            LEFT JOIN dynasties_{lang} d ON p.dynasty_id=d.id WHERE d.id IS NULL""").fetchone()[0]
        ntype = con.execute(f"""SELECT count(*) FROM poems_{lang} p
            LEFT JOIN poetry_types_{lang} t ON p.type_id=t.id
            WHERE p.type_id IS NOT NULL AND t.id IS NULL""").fetchone()[0]
        print(f"    {lang}: 孤儿作者 {orphan} ｜ 孤儿朝代 {nodyn} ｜ 孤儿体裁 {ntype}")
    print(f"\n耗时 {time.time()-t0:.0f}s → {a.out} "
          f"（{os.path.getsize(a.out)/1048576:.0f} MB）")


if __name__ == "__main__":
    main()
