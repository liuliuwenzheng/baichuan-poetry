#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全文检索索引构建器 —— 给百川库补上 FTS5。

**为什么单独一个脚本**：FTS 索引在旧库里是上游 DB 自带的，我们的构建脚本从未建过它，
于是 v0.3.0 用 `--cp-json` 重建后，库是「全的」但搜不了（`poem.py search` 直接报
`no such table: poems_fts_zh_hans`）。

**它是独立的一步，不是 `ingest.py` 的一部分**（有意如此）：建索引约 15 分钟，
库从 1.1 GB 涨到约 3.3 GB。只想拿数据、不要索引的人不该被迫等这 15 分钟。
不建也能查——`poem.py search` 与开放接口都会自动退回 `LIKE` 子串扫描，结果完整、只是慢。

设计（与上游 poetry.db 保持一致，CLI 无需改动）：

* 虚拟表 `poems_fts_{lang}(title, content_text)`，`tokenize='trigram'`
* `content_text` 是**扁平化**的正文（`json_each` 把 JSON 段落拼起来），
  这样 MATCH 命中的是诗本身，而不是 JSON 的方括号和引号
* `rowid` = `poems_{lang}.id`，可直接与主表 join
* 三个触发器（INSERT/UPDATE/DELETE）保持增量同步，将来改动数据无需重建

**trigram 的限制**：至少要 3 个字符才能 MATCH（单字/双字要用 LIKE 扫表）——
这是飞花令那条路的由来，不是 bug。查询时**不要**以为索引坏了：

    SELECT count(*) FROM poems_fts_zh_hans WHERE poems_fts_zh_hans MATCH '明月';   -- 0 条（2 字）
    SELECT count(*) FROM poems_fts_zh_hans WHERE poems_fts_zh_hans MATCH '明月几时有'; -- 13 条

用法：
    python tools/build_search.py                  # 建索引（已存在则重建）
    python tools/build_search.py --check          # 只体检，不写
    python tools/build_search.py --optimize       # 只做 optimize（紧缩索引）
"""
import argparse
import os
import sqlite3
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(HERE, "data", "baichuan.db")

LANGS = ("zh_hans", "zh_hant")

# 扁平化正文的 SQL 片段：把 poems.content（JSON 数组）拼成一个字符串
_FLAT = "COALESCE(group_concat(j.value, ''), '')"


def _fts_ddl(lang):
    """建虚拟表 + 三个同步触发器。与上游 poetry.db 的形态逐字一致。"""
    t, f = f"poems_{lang}", f"poems_fts_{lang}"
    return f"""
CREATE VIRTUAL TABLE IF NOT EXISTS {f} USING fts5(
    title, content_text, tokenize='trigram');

CREATE TRIGGER IF NOT EXISTS {t}_fts_ai AFTER INSERT ON {t} BEGIN
    INSERT INTO {f}(rowid, title, content_text)
    VALUES (new.id, new.title,
            (SELECT COALESCE(group_concat(value, ''), '') FROM json_each(new.content)));
END;

CREATE TRIGGER IF NOT EXISTS {t}_fts_ad AFTER DELETE ON {t} BEGIN
    DELETE FROM {f} WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS {t}_fts_au AFTER UPDATE ON {t} BEGIN
    DELETE FROM {f} WHERE rowid = old.id;
    INSERT INTO {f}(rowid, title, content_text)
    VALUES (new.id, new.title,
            (SELECT COALESCE(group_concat(value, ''), '') FROM json_each(new.content)));
END;
"""


def build(con, lang, force=True, quiet=False):
    """为一种文字版本建 FTS。返回行数；失败抛异常。"""
    t, f = f"poems_{lang}", f"poems_fts_{lang}"
    n_poems = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    if not n_poems:
        if not quiet:
            print(f"  {lang}: 主表为空，跳过")
        return 0

    # 触发器要在**灌数据之前**建好，之后的主表改动才会自动同步
    con.executescript(_fts_ddl(lang))
    con.commit()

    have = con.execute(f"SELECT count(*) FROM {f}").fetchone()[0]
    if have and not force:
        if not quiet:
            print(f"  {lang}: 已有 {have:,} 行，跳过（--force 可重建）")
        return have

    if have:
        # 重建：清空后再灌，避免残留已删诗句的索引
        con.execute(f"DELETE FROM {f}")
        con.commit()

    t0 = time.time()
    # 用 json_each 做表值 join 再 group，比逐行相关子查询快得多
    con.execute(f"""
        INSERT INTO {f}(rowid, title, content_text)
        SELECT p.id, p.title, {_FLAT}
        FROM {t} p, json_each(p.content) j
        GROUP BY p.id""")
    con.commit()
    n = con.execute(f"SELECT count(*) FROM {f}").fetchone()[0]
    if not quiet:
        print(f"  {lang}: {n:,} 行 / {n_poems:,} 首  →  {time.time() - t0:.1f}s")
    return n


def optimize(con, lang, quiet=False):
    """紧缩索引（合并 b-tree 段），能显著缩小体积、加快查询。"""
    f = f"poems_fts_{lang}"
    t0 = time.time()
    before = os.path.getsize(con.execute("PRAGMA database_list").fetchone()[2])
    con.execute(f"INSERT INTO {f}({f}) VALUES('optimize')")
    con.commit()
    after = os.path.getsize(con.execute("PRAGMA database_list").fetchone()[2])
    if not quiet:
        print(f"  {lang}: optimize {time.time() - t0:.1f}s ｜ 库 {before / 1e6:.0f} → {after / 1e6:.0f} MB")


def check(con):
    """体检：索引行数、抽样 MATCH、看是否与主表一致。"""
    print("FTS 体检：")
    ok = True
    for lang in LANGS:
        t, f = f"poems_{lang}", f"poems_fts_{lang}"
        try:
            n_p = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            n_f = con.execute(f"SELECT count(*) FROM {f}").fetchone()[0]
        except sqlite3.OperationalError as e:
            print(f"  ❌ {lang}: {e}")
            ok = False
            continue
        same = "✅" if n_p == n_f else "❌"
        if n_p != n_f:
            ok = False
        print(f"  {same} {lang}: 主表 {n_p:,} ｜ 索引 {n_f:,}")
        # 真搜一下，别只看行数。
        # **探针必须 ≥3 个字**：trigram 按 3 字切分，拿「明月」这种 2 字词去试
        # 必然 0 条 —— 那会把好索引报成坏索引，害人去「修」一个没坏的东西。
        for probe in ("明月几时有", "大江东去"):
            try:
                hit = con.execute(
                    f"SELECT count(*) FROM {f} WHERE {f} MATCH ?", (probe,)).fetchone()[0]
                if not hit:
                    ok = False
                print(f"      {'✅' if hit else '❌'} MATCH '{probe}' → {hit:,} 首")
            except sqlite3.OperationalError as e:
                print(f"      ❌ MATCH '{probe}': {e}")
                ok = False
        # 对照组：2 字应当为 0。写出来，免得下次看到 0 又以为坏了。
        z = con.execute(f"SELECT count(*) FROM {f} WHERE {f} MATCH ?",
                        ("明月",)).fetchone()[0]
        print(f"      ·（对照）MATCH '明月'（2 字）→ {z} 首 —— trigram 需 ≥3 字，本应为 0")
    # 触发器在不在
    trg = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE '%_fts_%'")}
    want = {f"poems_{l}_fts_{s}" for l in LANGS for s in ("ai", "ad", "au")}
    missing = want - trg
    print(f"  {'✅' if not missing else '❌'} 同步触发器 {len(trg)}/{len(want)}"
          + (f"（缺：{', '.join(sorted(missing))}）" if missing else ""))
    ok = ok and not missing
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--check", action="store_true", help="只体检")
    ap.add_argument("--optimize", action="store_true", help="只做 optimize")
    ap.add_argument("--no-optimize", action="store_true", help="不 optimize")
    ap.add_argument("--no-force", action="store_true", help="已存在则跳过")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit(f"找不到库：{a.db}")
    size0 = os.path.getsize(a.db)
    con = sqlite3.connect(a.db)
    con.execute("PRAGMA journal_mode=DELETE")   # WAL 库没法被只读连接打开

    if a.check:
        sys.exit(0 if check(con) else 1)

    if a.optimize:
        for lang in LANGS:
            optimize(con, lang)
        con.close()
        sys.exit(0)

    print(f"建全文检索索引：{a.db}  （当前 {size0 / 1e6:.0f} MB）")
    for lang in LANGS:
        build(con, lang, force=not a.no_force)
    if not a.no_optimize:
        print("optimize（紧缩索引）…")
        for lang in LANGS:
            optimize(con, lang)
    con.close()
    print(f"完成 → {os.path.getsize(a.db) / 1e6:.0f} MB"
          f"（+{(os.path.getsize(a.db) - size0) / 1e6:.0f} MB）")
    con = sqlite3.connect(a.db)
    ok = check(con)
    con.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
