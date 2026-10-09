#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诗泉 · 中国古诗词查询命令行（离线 SQLite / 在线 API 双通道）

离线（默认）：直查本地 poetry.db（371,313 首简体 / 377,210 首繁体）
在线：--online 走 https://poetry.palemoky.com/api

用法示例：
  python poem.py random                          # 随机一首
  python poem.py random --author 李白            # 指定作者随机取一首
  python poem.py random --char 春                # 飞花令（含「春」的诗句）
  python poem.py search 明月光                   # 全文搜索（FTS，≥3 字）
  python poem.py search 春 --like                # 单字/双字用 LIKE 扫描（约 0.35s）
  python poem.py poem 28825                      # 按 id 取诗
  python poem.py author 李白 --limit 5           # 作者作品
  python poem.py stats                           # 数据统计
  python poem.py dynasties                       # 朝代列表
  python poem.py types                           # 体裁列表
  python poem.py random --online --author 李白   # 在线通道
  python poem.py --hant random                   # 繁体

意象 / 主题（走本地倒排索引，毫秒级）：
  python poem.py image 月                        # 含「月」的诗
  python poem.py image 月 花 酒 --limit 10       # 多意象，按出现次数排
  python poem.py image 月 --by hits              # 按命中意象种数排
  python poem.py image 月 雪 --random            # 命中集合里随机取
  python poem.py theme 思乡 --limit 5            # 主题（内置 12 类词典）
  python poem.py kstat 月                        # 意象词频/作者/朝代分布
  python poem.py lines 春 --limit 15             # 取含「春」的句子（飞花令）
  python poem.py keywords                        # 列出全部意象/主题词
"""
import argparse
import json
import os
import sqlite3
import sys
import urllib.parse
import urllib.request
import unicodedata

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEPLOY_DIR = r"E:\AI-ku\项目\chinese-poetry-api"


def _data_file(name):
    """先看脚本旁边的 data/，没有再回落到部署目录（技能里的副本也能直接用）"""
    p = os.path.join(HERE, "data", name)
    if os.path.exists(p):
        return p
    alt = os.path.join(DEPLOY_DIR, "data", name)
    return alt if os.path.exists(alt) else p


DEFAULT_DB = _data_file("poetry.db")
KEYWORD_DB = _data_file("keywords.db")
ONLINE_BASE = "https://poetry.palemoky.com"

try:
    sys.path.insert(0, HERE)
    from poem_keywords import IMAGERY, THEMES  # noqa: E402
except Exception:                                    # 词表丢了也能跑基本命令
    IMAGERY, THEMES = [], {}

TYPE_ID = {"唐诗": 10, "五言绝句": 11, "七言绝句": 12, "五言律诗": 13, "七言律诗": 14,
           "五言古诗": 15, "七言古诗": 16, "乐府诗": 17, "宋词": 20, "五代词": 21,
           "元曲": 30, "蒙学": 40, "诗经": 50, "论语": 60, "楚辞": 70, "四书五经": 80, "其他": 99}


# ---------------------------------------------------------------- 展示
def _w(s):
    """终端显示宽度（中文按 2 算，否则表格对不齐）"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s, n, right=False):
    sp = " " * max(0, n - _w(s))
    return (sp + str(s)) if right else (str(s) + sp)


def render_astat(d, limit=12, by="lift"):
    profs = [p for p in d["profiles"] if not p.get("missing")]
    missing = [p["name"] for p in d["profiles"] if p.get("missing")]
    if missing:
        print(f"（未找到作者：{'、'.join(missing)}）")
    if not profs:
        return
    allp = d["all_poems"]

    if len(profs) == 1:
        p = profs[0]
        print(f"作者意象画像：{p['name']}    作品 {p['poems']:,} 首 ｜ 命中 {p['hit']}/{len(d['words'])} 个意象\n")
        key = (lambda kv: -kv[1]["lift"]) if by == "lift" else (lambda kv: -kv[1]["poems"])
        print(_pad("意象", 8) + _pad("含该意象", 10, True) + _pad("本人占比", 10, True)
              + _pad("全库占比", 10, True) + _pad("偏好倍数", 11, True))
        print("-" * 49)
        for w, v in sorted(p["data"].items(), key=key)[:limit]:
            bp = d["base"].get(w, (0, 0))[0] / allp
            print(_pad(w, 8) + _pad(f"{v['poems']:,}", 10, True)
                  + _pad(f"{v['share']*100:.1f}%", 10, True)
                  + _pad(f"{bp*100:.1f}%", 10, True)
                  + _pad(f"{v['lift']:.2f}×", 11, True))
        if by == "lift":
            print("\n（「偏好倍数」= 该作者含该意象的诗占比 ÷ 全库占比，>1 即比平均更偏爱；"
                  "这样比绝对数量公平）")
        return

    # ---- 多作者对比：按「区分度」排序（lift 极差最大的排前面）----
    def spread(w):
        ls = [p["data"].get(w, {}).get("lift", 0) for p in profs]
        return max(ls) - min(ls)

    if by == "count":
        words = sorted(d["words"], key=lambda w: -sum(p["data"].get(w, {}).get("poems", 0) for p in profs))
    else:
        words = sorted(d["words"], key=spread, reverse=True)

    colw = 13
    print("作者意象对比（偏好倍数，括号内为含该意象的诗数；★=该行最高）\n")
    print(_pad("作品数", 8) + "".join(_pad(f"{p['poems']:,}", colw, True) for p in profs))
    print(_pad("意象", 8) + "".join(_pad(p["name"][:6], colw, True) for p in profs))
    print("-" * (8 + colw * len(profs)))
    for w in words[:limit]:
        vals = [p["data"].get(w, {}).get("lift", 0) for p in profs]
        mx = max(vals) if vals else 0
        cells = []
        for p, v in zip(profs, vals):
            n = p["data"].get(w, {}).get("poems", 0)
            cells.append(f"{v:.2f}×({n})" + ("★" if v == mx and mx > 0 else ""))
        print(_pad(w, 8) + "".join(_pad(c, colw, True) for c in cells))
    print("\n（已按「作者间差异最大」排序：排在前面的意象最能区分这几位诗人的风格）")


def fmt(poem, show_meta=True):
    if not poem:
        return "（未找到）"
    lines = poem.get("content") or []
    head = poem.get("title", "")
    out = [f"《{head}》"]
    if show_meta:
        au = (poem.get("author") or {}).get("name") or "佚名"
        dy = (poem.get("dynasty") or {}).get("name") or ""
        ty = (poem.get("type") or {}).get("name") or ""
        tag = " · ".join(x for x in (dy, au, ty) if x)
        out[0] = f"《{head}》  [{tag}]  #{poem.get('id','')}"
    out += ["  " + str(l) for l in lines]
    return "\n".join(out)


# ---------------------------------------------------------------- 离线
class Local:
    def __init__(self, db_path, hant=False, keyword_db=None):
        if not os.path.exists(db_path):
            sys.exit(f"找不到数据库：{db_path}\n请先下载 poetry.db（见 README.md）或加 --online 用在线接口。")
        self.lang = "zh_hant" if hant else "zh_hans"
        self.db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        # 意象/主题倒排索引（可选；没有就退化成 LIKE 全表扫描）
        kw = keyword_db or KEYWORD_DB
        self.has_kw = os.path.exists(kw)
        if self.has_kw:
            try:
                self.db.execute("ATTACH DATABASE ? AS kw", (kw,))
            except sqlite3.Error:
                self.has_kw = False

    def _rows(self, sql, args=()):
        return [dict(r) for r in self.db.execute(sql, args)]

    def _poems(self, where="1=1", args=(), limit=1, order="RANDOM()"):
        lang = self.lang
        sql = f"""
        SELECT p.id, p.title, p.content,
               a.name AS author_name, a.id AS author_id,
               d.name AS dynasty_name, d.id AS dynasty_id,
               t.name AS type_name, t.id AS type_id
        FROM poems_{lang} p
        LEFT JOIN authors_{lang} a ON a.id = p.author_id
        LEFT JOIN dynasties_{lang} d ON d.id = p.dynasty_id
        LEFT JOIN poetry_types_{lang} t ON t.id = p.type_id
        WHERE {where} ORDER BY {order} LIMIT ?"""
        out = []
        for r in self._rows(sql, tuple(args) + (limit,)):
            out.append({
                "id": r["id"], "title": r["title"],
                "content": json.loads(r["content"]),
                "author": {"id": r["author_id"], "name": r["author_name"]},
                "dynasty": {"id": r["dynasty_id"], "name": r["dynasty_name"]},
                "type": {"id": r["type_id"], "name": r["type_name"]},
            })
        return out

    def random(self, author=None, type_=None, dynasty=None, char=None, limit=1):
        self._ensure()
        lang = self.lang
        w, a = [], []
        if char:
            w.append("p.content LIKE ?")
            a.append(f"%{char}%")
        if author:
            w.append("a.name = ?")
            a.append(author)
        if dynasty:
            w.append("d.name = ?")
            a.append(dynasty)
        if type_:
            w.append("t.name = ?")
            a.append(type_)
        return self._poems(" AND ".join(w) if w else "1=1", a, limit)

    def _ensure(self):
        pass

    def search(self, q, limit=10, like=False, type_=None):
        lang = self.lang
        if like or len(q) < 3:
            return self._poems("p.content LIKE ? OR p.title LIKE ?",
                               (f"%{q}%", f"%{q}%"), limit, order="p.id")
        w = [f"p.id IN (SELECT rowid FROM poems_fts_{lang} WHERE poems_fts_{lang} MATCH ?)"]
        a = [q]
        if type_:
            w.append("t.name = ?")
            a.append(type_)
        return self._poems(" AND ".join(w), a, limit, order="p.id")

    def poem(self, pid):
        return self._poems("p.id = ?", (pid,), 1, order="p.id")

    def author(self, name, limit=10):
        lang = self.lang
        rows = self._rows(f"""SELECT a.id, a.name, a.description,
                               d.name AS dynasty,
                               (SELECT count(*) FROM poems_{lang} p WHERE p.author_id = a.id) AS poem_count
                               FROM authors_{lang} a
                               LEFT JOIN dynasties_{lang} d ON d.id=a.dynasty_id
                               WHERE a.name = ? ORDER BY poem_count DESC""", (name,))
        poems = self._poems("a.name = ?", (name,), limit, order="RANDOM()")
        return {"authors": rows, "poems": poems}

    def stats(self):
        lang = self.lang
        rows = self._rows(f"""SELECT t.name AS type, count(*) n
                              FROM poems_{lang} p JOIN poetry_types_{lang} t ON t.id=p.type_id
                              GROUP BY t.id ORDER BY n DESC""")
        total = self.db.execute(f"SELECT count(*) FROM poems_{lang}").fetchone()[0]
        au = self.db.execute(f"SELECT count(*) FROM authors_{lang}").fetchone()[0]
        dyn = self._rows(f"""SELECT d.name AS dynasty, count(*) n FROM poems_{lang} p
                             JOIN dynasties_{lang} d ON d.id=p.dynasty_id
                             GROUP BY d.id ORDER BY n DESC""")
        return {"poems": total, "authors": au, "by_type": rows, "by_dynasty": dyn}

    def dynasties(self):
        return self._rows(f"SELECT id,name,name_en,start_year,end_year FROM dynasties_{self.lang} ORDER BY id")

    def types(self):
        return self._rows(f"""SELECT id,name,category,lines,chars_per_line,description
                              FROM poetry_types_{self.lang} ORDER BY id""")

    # ---------------------------------------------------- 意象 / 主题（走倒排索引）
    def _poems_by_ids(self, ids, meta=None):
        if not ids:
            return []
        lang = self.lang
        ph = ",".join("?" * len(ids))
        sql = f"""
        SELECT p.id, p.title, p.content,
               a.name AS author_name, a.id AS author_id,
               d.name AS dynasty_name, d.id AS dynasty_id,
               t.name AS type_name, t.id AS type_id
        FROM poems_{lang} p
        LEFT JOIN authors_{lang} a ON a.id = p.author_id
        LEFT JOIN dynasties_{lang} d ON d.id = p.dynasty_id
        LEFT JOIN poetry_types_{lang} t ON t.id = p.type_id
        WHERE p.id IN ({ph})"""
        got = {r["id"]: r for r in self._rows(sql, ids)}
        out = []
        for pid in ids:
            r = got.get(pid)
            if not r:
                continue
            p = {"id": r["id"], "title": r["title"], "content": json.loads(r["content"]),
                 "author": {"id": r["author_id"], "name": r["author_name"]},
                 "dynasty": {"id": r["dynasty_id"], "name": r["dynasty_name"]},
                 "type": {"id": r["type_id"], "name": r["type_name"]}}
            if meta and pid in meta:
                p["score"] = meta[pid]
            out.append(p)
        return out

    def _rank_kw(self, words, limit, by="total", random_pick=False):
        """倒排索引打分；by='total' 按出现次数，by='hits' 按命中词种数"""
        lang = self.lang
        ph = ",".join("?" * len(words))
        order = "RANDOM()" if random_pick else ("hits DESC, total DESC" if by == "hits" else "total DESC, hits DESC")
        rows = self._rows(
            f"""SELECT poem_id, hits, total FROM (
                    SELECT poem_id, count(*) hits, sum(n) total FROM kw.keywords
                    WHERE lang=? AND word IN ({ph}) GROUP BY poem_id)
                ORDER BY {order} LIMIT ?""",
            tuple([lang] + list(words) + [limit]))
        meta = {r["poem_id"]: r for r in rows}
        return self._poems_by_ids([r["poem_id"] for r in rows], meta)

    def _rank_like(self, words, limit, by="total", random_pick=False):
        """无索引时的回退：全表 LIKE + 打分（371k 行约 1s），无 index 时用"""
        lang = self.lang
        score = " + ".join(["(CASE WHEN p.content LIKE ? THEN 1 ELSE 0 END)"] * len(words))
        total = " + ".join([f"(length(p.content)-length(replace(p.content,?,'')))/{len(w)}" for w in words])
        where = " OR ".join(["p.content LIKE ?"] * len(words))
        order = "RANDOM()" if random_pick else ("hits DESC, total DESC" if by == "hits" else "total DESC, hits DESC")
        args = ([f"%{w}%" for w in words] + list(words)
                + [f"%{w}%" for w in words] + [limit])
        rows = self._rows(
            f"""SELECT p.id, ({score}) AS hits, ({total}) AS total
                FROM poems_{lang} p WHERE ({where}) ORDER BY {order} LIMIT ?""", tuple(args))
        meta = {r["id"]: r for r in rows}
        return self._poems_by_ids([r["id"] for r in rows], meta)

    def _rank(self, words, limit, by="total", random_pick=False):
        return (self._rank_kw if self.has_kw else self._rank_like)(words, limit, by, random_pick)

    def _pick(self, words, limit, by="total", random_pick=False, max_lines=20):
        """捞诗并过滤掉超长组诗（寒山《诗三百三首》这类会把结果淹没）"""
        pool = limit * 10 if max_lines else limit
        res = self._rank(list(words), pool, by, random_pick)
        if max_lines:
            res = [p for p in res if len(p.get("content") or []) <= max_lines]
            if random_pick:
                import random as _r
                _r.shuffle(res)
        return res[:limit]

    def image(self, words, limit=10, by="total", random_pick=False, max_lines=20):
        return self._pick(words, limit, by, random_pick, max_lines)

    def theme(self, name, limit=10, words=None, max_lines=20):
        ws = words or THEMES.get(name) or [name]
        return self._pick(ws, limit, "hits", False, max_lines)

    def kstat(self, word, top=8):
        """单个意象的词频 / 作者 / 朝代 / 体裁分布"""
        lang = self.lang
        out = {"word": word}
        if self.has_kw:
            r = self.db.execute(
                "SELECT count(*), COALESCE(sum(n),0) FROM kw.keywords WHERE lang=? AND word=?",
                (lang, word)).fetchone()
            out["poems"], out["occurrences"] = r[0], r[1]
            frm = f"FROM kw.keywords k JOIN poems_{lang} p ON p.id=k.poem_id"
            wh = "WHERE k.lang=? AND k.word=?"
            out["by_author"] = self._rows(
                f"""SELECT a.name, count(*) c {frm} JOIN authors_{lang} a ON a.id=p.author_id
                    {wh} GROUP BY a.id ORDER BY c DESC LIMIT ?""", (lang, word, top))
            out["by_dynasty"] = self._rows(
                f"""SELECT d.name, count(*) c {frm} JOIN dynasties_{lang} d ON d.id=p.dynasty_id
                    {wh} GROUP BY d.id ORDER BY c DESC LIMIT ?""", (lang, word, top))
            out["by_type"] = self._rows(
                f"""SELECT t.name, count(*) c {frm} JOIN poetry_types_{lang} t ON t.id=p.type_id
                    {wh} GROUP BY t.id ORDER BY c DESC LIMIT ?""", (lang, word, top))
        else:
            out["poems"] = self.db.execute(
                f"SELECT count(*) FROM poems_{lang} WHERE content LIKE ?", (f"%{word}%",)).fetchone()[0]
        out["samples"] = self.lines_with(word, 5)
        return out

    # ---------------------------------------------------- 作者意象画像
    def _author_ids(self, name):
        return [r["id"] for r in self._rows(
            f"SELECT id FROM authors_{self.lang} WHERE name = ?", (name,))]

    def _baseline(self, ws):
        """全库基线：每个词在全库多少首诗里出现（优先用物化表）"""
        lang = self.lang
        ph = ",".join("?" * len(ws))
        try:
            rows = self._rows(f"""SELECT word, poems, total FROM kw.keyword_stats
                                  WHERE lang=? AND word IN ({ph})""", tuple([lang] + list(ws)))
            if rows:
                return {r["word"]: (r["poems"], r["total"]) for r in rows}
        except sqlite3.Error:
            pass
        return {r["word"]: (r["poems"], r["total"]) for r in self._rows(
            f"""SELECT word, count(*) poems, sum(n) total FROM kw.keywords
                WHERE lang=? AND word IN ({ph}) GROUP BY word""", tuple([lang] + list(ws)))}

    def astat(self, names, limit=12, words=None, by="lift"):
        """作者意象画像。一个作者=画像，多个作者=并排对比。

        lift（偏好倍数）= 该作者作品里含该意象的比例 ÷ 全库该意象的比例，
        >1 表示比平均更偏爱 —— 用它比较产量悬殊的作者（陆游 9,413 首 vs 李白 1,863 首）。
        """
        lang = self.lang
        ws = list(words or IMAGERY)
        ph = ",".join("?" * len(ws))
        all_poems = self.db.execute(f"SELECT count(*) FROM poems_{lang}").fetchone()[0]
        base = self._baseline(ws)
        if not base:
            return None

        profiles = []
        for name in names:
            aids = self._author_ids(name)
            if not aids:
                profiles.append({"name": name, "missing": True})
                continue
            iph = ",".join("?" * len(aids))
            npoems = self.db.execute(
                f"SELECT count(*) FROM poems_{lang} WHERE author_id IN ({iph})", tuple(aids)
            ).fetchone()[0]
            try:
                rows = self._rows(
                    f"""SELECT word, sum(poems) np, sum(total) tot FROM kw.author_keywords
                        WHERE lang=? AND author_id IN ({iph}) AND word IN ({ph})
                        GROUP BY word""", tuple([lang] + aids + list(ws)))
            except sqlite3.Error:                     # 没建作者表 → 现算
                rows = self._rows(
                    f"""SELECT k.word, count(*) np, sum(k.n) tot FROM kw.keywords k
                        JOIN poems_{lang} p ON p.id = k.poem_id
                        WHERE k.lang=? AND p.author_id IN ({iph}) AND k.word IN ({ph})
                        GROUP BY k.word""", tuple([lang] + aids + list(ws)))
            data = {}
            for r in rows:
                bp = base.get(r["word"], (0, 0))[0]
                share = r["np"] / npoems if npoems else 0
                bshare = bp / all_poems if all_poems else 0
                data[r["word"]] = {
                    "poems": r["np"], "total": r["tot"], "share": share,
                    "lift": (share / bshare) if bshare else 0,
                }
            profiles.append({"name": name, "poems": npoems, "data": data, "hit": len(data)})
        return {"profiles": profiles, "base": base, "all_poems": all_poems, "words": ws}

    def lines_with(self, word, limit=20, random_pick=True):
        if self.has_kw:
            rows = self._rows(
                "SELECT poem_id FROM kw.keywords WHERE lang=? AND word=? ORDER BY RANDOM() LIMIT ?",
                (self.lang, word, limit * 4))
            poems = self._poems_by_ids([r["poem_id"] for r in rows])
        else:
            poems = self._poems("p.content LIKE ?", (f"%{word}%",), limit * 4,
                                order="RANDOM()" if random_pick else "p.id")
        out = []
        for p in poems:
            for line in p["content"]:
                if word in line:
                    out.append({"line": line, "title": p["title"],
                                "author": (p["author"] or {}).get("name"),
                                "dynasty": (p["dynasty"] or {}).get("name"),
                                "type": (p["type"] or {}).get("name"), "id": p["id"]})
                    if len(out) >= limit:
                        return out
        return out


class Online:
    def __init__(self, hant=False):
        self.lang = "zh-Hant" if hant else "zh-Hans"

    def _get(self, path, params):
        params = {k: v for k, v in params.items() if v not in (None, "")}
        params["lang"] = self.lang
        url = ONLINE_BASE + path + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"accept": "application/json",
                                                   "User-Agent": "poem-cli/1.0"})
        # 走系统代理（若设置了 HTTPS_PROXY）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler())
        with opener.open(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))

    def random(self, author=None, type_=None, dynasty=None, char=None, limit=1):
        d = self._get("/api/poems/random", {"author": author, "type": type_,
                                            "dynasty": dynasty, "char": char})
        return [d["data"]] if d.get("data") else []

    def search(self, q, limit=10, like=False, type_=None):
        d = self._get("/api/search", {"q": q, "page": 1, "page_size": limit})
        return (d.get("data") or [])[:limit]

    def poem(self, pid):
        d = self._get("/api/poems", {"page": 1, "page_size": 1})
        for p in (d.get("data") or []):
            if p.get("id") == pid:
                return [p]
        return []

    def author(self, name, limit=10):
        return {"authors": [], "poems": self.random(author=name, limit=limit)}

    def stats(self):
        d = self._get("/api/stats", {})
        return d.get("data")

    def dynasties(self):
        return self._get("/api/dynasties", {}).get("data") or []

    def types(self):
        return self._get("/api/types", {}).get("data") or []

    # 在线版没有倒排索引，只能用 /api/search 凑合（能力降级，仅应急）
    def image(self, words, limit=10, by="total", random_pick=False, max_lines=20):
        return self.search(words[0], limit)

    def theme(self, name, limit=10, words=None, max_lines=20):
        ws = words or THEMES.get(name) or [name]
        out, seen = [], set()
        for w in ws[:4]:
            for p in self.search(w, limit):
                if p.get("id") not in seen:
                    seen.add(p.get("id"))
                    out.append(p)
                if len(out) >= limit:
                    return out
        return out

    def kstat(self, word, top=8):
        return {"word": word, "poems": "—（在线版不支持统计）", "samples": self.lines_with(word, 5)}

    def astat(self, names, limit=12, words=None, by="lift"):
        return None                                   # 在线版没有本地索引，不支持

    def lines_with(self, word, limit=20, random_pick=True):
        out = []
        for p in self.search(word, limit * 2):
            for line in (p.get("content") or []):
                if word in line:
                    out.append({"line": line, "title": p.get("title"),
                                "author": (p.get("author") or {}).get("name"),
                                "dynasty": (p.get("dynasty") or {}).get("name"),
                                "type": (p.get("type") or {}).get("name"), "id": p.get("id")})
                    if len(out) >= limit:
                        return out
        return out


# ---------------------------------------------------------------- main
def build(args):
    hant = args.hant
    if args.online:
        return Online(hant)
    return Local(args.db, hant)


def main():
    p = argparse.ArgumentParser(description="中国古诗词查询（诗泉）")
    p.add_argument("--db", default=DEFAULT_DB, help="本地 poetry.db 路径")
    p.add_argument("--online", action="store_true", help="改用在线 API")
    p.add_argument("--hant", action="store_true", help="繁体中文")
    p.add_argument("--limit", type=int, default=5, help="返回条数（默认 5）")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("random", help="随机诗词")
    r.add_argument("--author"); r.add_argument("--type", dest="type_")
    r.add_argument("--dynasty"); r.add_argument("--char")
    r.add_argument("--limit", type=int, default=1)

    s = sub.add_parser("search", help="搜索")
    s.add_argument("q"); s.add_argument("--like", action="store_true")
    s.add_argument("--type", dest="type_")
    s.add_argument("--limit", type=int, default=5)

    g = sub.add_parser("poem", help="按 id 取诗"); g.add_argument("id", type=int)
    a = sub.add_parser("author", help="作者"); a.add_argument("name")
    a.add_argument("--limit", type=int, default=5)
    sub.add_parser("stats", help="统计")
    sub.add_parser("dynasties", help="朝代列表")
    sub.add_parser("types", help="体裁列表")
    sub.add_parser("keywords", help="列出内置意象/主题词表")

    im = sub.add_parser("image", help="按意象捞诗（可给多个）")
    im.add_argument("words", nargs="+")
    im.add_argument("--limit", type=int, default=10)
    im.add_argument("--by", choices=["total", "hits"], default="total",
                    help="排序依据：total=出现次数（默认），hits=命中意象种数")
    im.add_argument("--random", action="store_true", help="在命中集合里随机取")
    im.add_argument("--max-lines", type=int, default=20,
                    help="跳过超过 N 句的诗，0=不限（默认 20，挡掉寒山《诗三百三首》这类长组诗）")

    th = sub.add_parser("theme", help="按主题捞诗")
    th.add_argument("name")
    th.add_argument("--limit", type=int, default=10)
    th.add_argument("--words", help="自定义特征词，逗号分隔（覆盖内置词典）")
    th.add_argument("--max-lines", type=int, default=20, help="跳过超过 N 句的诗，0=不限")

    ks = sub.add_parser("kstat", help="意象词频/作者/朝代统计")
    ks.add_argument("word")
    ks.add_argument("--limit", type=int, default=8)

    ast = sub.add_parser("astat", help="作者意象画像 / 多作者风格对比")
    ast.add_argument("names", nargs="+", help="一个=画像，多个=并排对比")
    ast.add_argument("--limit", type=int, default=12)
    ast.add_argument("--words", help="限定意象，逗号分隔（默认全部 75 个）")
    ast.add_argument("--by", choices=["lift", "count"], default="lift",
                     help="lift=偏好倍数（默认，消除产量差异）｜count=绝对诗数")

    ln = sub.add_parser("lines", help="取含某字的句子（飞花令）")
    ln.add_argument("word")
    ln.add_argument("--limit", type=int, default=20)
    ln.add_argument("--seq", action="store_true", help="按 id 顺序取，而非随机")

    args = p.parse_args()
    src = build(args)

    if args.cmd == "random":
        for x in src.random(args.author, args.type_, args.dynasty, args.char,
                            getattr(args, "limit", 1) or 1):
            print(fmt(x)); print()
    elif args.cmd == "search":
        res = src.search(args.q, args.limit, getattr(args, "like", False), getattr(args, "type_", None))
        print(f"共返回 {len(res)} 条\n")
        for x in res:
            print(fmt(x)); print()
    elif args.cmd == "poem":
        for x in src.poem(args.id):
            print(fmt(x))
    elif args.cmd == "author":
        d = src.author(args.name, args.limit)
        for au in d["authors"]:
            print(f"#{au['id']} {au['name']} · {au.get('dynasty') or ''} · 作品 {au.get('poem_count') or '?'} 首")
            if au.get("description"):
                print("  " + str(au["description"])[:200])
        print()
        for x in d["poems"]:
            print(fmt(x)); print()
    elif args.cmd == "stats":
        d = src.stats()
        if isinstance(d, dict) and "by_type" in d:
            print(f"诗词 {d['poems']:,} 首 · 作者 {d['authors']:,} 人")
            print("\n按体裁：")
            for r_ in d["by_type"]:
                print(f"  {r_['type']:<8} {r_['n']:>7,}")
            print("\n按朝代：")
            for r_ in d["by_dynasty"]:
                print(f"  {r_['dynasty']:<6} {r_['n']:>7,}")
        else:
            print(json.dumps(d, ensure_ascii=False, indent=2))
    elif args.cmd == "dynasties":
        for d in src.dynasties():
            print(f"#{d['id']} {d['name']} ({d.get('name_en')}) {d.get('start_year')}~{d.get('end_year')}")
    elif args.cmd == "types":
        for t in src.types():
            print(f"#{t['id']} {t['name']} · {t.get('category')} {t.get('description') or ''}")
    elif args.cmd == "keywords":
        print("意象（image 直接查）：")
        print("  " + "  ".join(IMAGERY))
        print("\n主题（theme 查）：")
        for k, v in THEMES.items():
            print(f"  {k:<5} " + " ".join(v))
    elif args.cmd == "image":
        res = src.image(args.words, args.limit, args.by, args.random, args.max_lines)
        tag = "" if getattr(src, "has_kw", True) else "（无索引，LIKE 全表回退，约 1s）"
        if args.max_lines:
            tag += f"；已挡掉 {args.max_lines} 句以上的组诗"
        print(f"意象「{'、'.join(args.words)}」命中 {len(res)} 首{tag}\n")
        for x in res:
            s = x.get("score")
            extra = f"   ↳ 命中 {s['hits']} 个意象 · 共出现 {s['total']} 次" if s else ""
            print(fmt(x) + ("\n" + extra if extra else ""))
            print()
    elif args.cmd == "theme":
        ws = [w.strip() for w in args.words.split(",")] if args.words else None
        res = src.theme(args.name, args.limit, ws, args.max_lines)
        print(f"主题「{args.name}」命中 {len(res)} 首"
              + (f"（自定义词：{'、'.join(ws)}）" if ws else "") + "\n")
        for x in res:
            s = x.get("score")
            extra = f"   ↳ 命中 {s['hits']}/{len(ws or THEMES.get(args.name, []))} 个特征词 · 共出现 {s['total']} 次" if s else ""
            print(fmt(x) + ("\n" + extra if extra else ""))
            print()
    elif args.cmd == "kstat":
        d = src.kstat(args.word, args.limit)
        print(f"意象「{d['word']}」：{d.get('poems', '?')} 首诗中出现"
              + (f"，累计 {d['occurrences']:,} 次" if d.get("occurrences") else ""))
        for key, label in (("by_author", "作者 Top"), ("by_dynasty", "朝代"), ("by_type", "体裁")):
            if d.get(key):
                print(f"\n{label}：")
                for r_ in d[key]:
                    print(f"  {r_['name']:<10} {r_['c']:>6,}")
        if d.get("samples"):
            print("\n句子示例：")
            for x in d["samples"]:
                print(f"  {x['line']}   —— {x['dynasty']}·{x['author']}《{x['title']}》#{x['id']}")
    elif args.cmd == "lines":
        res = src.lines_with(args.word, args.limit, not args.seq)
        print(f"含「{args.word}」的句子 {len(res)} 句\n")
        for x in res:
            print(f"  {x['line']}\n      —— {x['dynasty']}·{x['author']}《{x['title']}》#{x['id']}")
    elif args.cmd == "astat":
        ws = [w.strip() for w in args.words.split(",")] if args.words else None
        d = src.astat(args.names, args.limit, ws, args.by)
        if d is None:
            print("作者画像需要本地索引：先跑 `python build_index.py`（会建 author_keywords 表）")
        else:
            render_astat(d, args.limit, args.by)


if __name__ == "__main__":
    main()
