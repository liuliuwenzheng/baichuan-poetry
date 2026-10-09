#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""百川 · 架构与数据完整性校验

把踩过的坑变成**自动断言**，以后谁再犯立刻红。
退出码 0 = 全通过；非 0 = 有断言失败（可以直接挂进 CI）。

校验项：
  A. SQLite 完整性（quick_check）
  B. 孤儿引用 = 0（作者 / 朝代 / 体裁形式 / 体裁轴）
  C. 【架构】体裁表与体裁轴表的名字里**不得出现朝代名**
     —— 「唐诗」「宋词」这类命名就是把朝代压进了体裁维度；
        当年「宋词」表里混着 257 首清词（纳兰性德）就是这么来的。
  D. 【架构】体裁轴必须真的正交：同一个体裁要能跨多个朝代取到，
        同一个朝代要能取到多个体裁。不是正交就会被这条抓住。
  E. 简繁两库计数一致性
  F. content_hash 抽样重算（哈希 = sha256(段落拼接)，防内容与哈希脱钩）

用法：
    python tools/verify.py                 # 默认校验 data/baichuan.db
    python tools/verify.py --db other.db
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "baichuan.db")

# 朝代名 —— 一旦出现在**体裁名**里就是架构退化
DYNASTY_WORDS = [
    "先秦", "秦", "汉", "魏晋", "晋", "南北朝", "隋", "唐", "五代", "宋",
    "辽", "金", "元", "明", "清", "近现代", "当代", "现代", "民国",
]

FAILS = []
NOTES = []


def check(cond, label, detail=""):
    mark = "✅" if cond else "❌"
    print(f"  {mark} {label}" + (f" —— {detail}" if detail else ""))
    if not cond:
        FAILS.append(label)
    return cond


def note(msg):
    NOTES.append(msg)
    print(f"     · {msg}")


def content_hash(paras):
    return hashlib.sha256("".join(paras).encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--sample", type=int, default=300, help="哈希抽样条数")
    a = ap.parse_args()

    if not os.path.exists(a.db):
        sys.exit(f"找不到数据库：{a.db}")
    con = sqlite3.connect(a.db)
    con.row_factory = sqlite3.Row

    print(f"\n百川 · 架构与数据完整性校验")
    print(f"库：{a.db}  ({os.path.getsize(a.db) / 1e6:.1f} MB)\n")

    # ---------------------------------------------------------------- A
    print("A. SQLite 完整性")
    qc = con.execute("PRAGMA quick_check").fetchone()[0]
    check(qc == "ok", "quick_check", qc)

    # ---------------------------------------------------------------- B
    print("\nB. 孤儿引用（应为 0）")
    tables = {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    has_genres = "genres_zh_hans" in tables
    for lang in ("zh_hans", "zh_hant"):
        for col, ref in (("author_id", "authors"), ("dynasty_id", "dynasties"),
                         ("type_id", "poetry_types")):
            if f"{ref}_{lang}" not in tables:
                continue
            n = con.execute(
                f"SELECT count(*) FROM poems_{lang} p "
                f"WHERE p.{col} IS NOT NULL AND NOT EXISTS "
                f"(SELECT 1 FROM {ref}_{lang} x WHERE x.id = p.{col})").fetchone()[0]
            check(n == 0, f"{lang} · {col} 孤儿", f"{n:,}")
        if has_genres:
            n = con.execute(
                f"SELECT count(*) FROM poems_{lang} p WHERE p.genre_id IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM genres_{lang} g WHERE g.id = p.genre_id)"
            ).fetchone()[0]
            check(n == 0, f"{lang} · genre_id 孤儿", f"{n:,}")
            nullg = con.execute(
                f"SELECT count(*) FROM poems_{lang} WHERE genre_id IS NULL").fetchone()[0]
            check(nullg == 0, f"{lang} · genre_id 为空", f"{nullg:,}")

    # ---------------------------------------------------------------- C
    print("\nC. 【架构】体裁名里不得含朝代名")
    for tbl in ("poetry_types_zh_hans", "poetry_types_zh_hant",
                "genres_zh_hans", "genres_zh_hant"):
        if tbl not in tables:
            continue
        bad = []
        for r in con.execute(f"SELECT id,name FROM {tbl}"):
            hits = [w for w in DYNASTY_WORDS if w in (r["name"] or "")]
            if hits:
                bad.append(f"#{r['id']} {r['name']}（含 {'/'.join(hits)}）")
        check(not bad, f"{tbl} 无朝代名", "、".join(bad) if bad else "干净")
    # 旧模型的两条「化石」名，一旦回来就是回退
    for fossil in ("宋词", "唐诗"):
        n = con.execute(
            "SELECT count(*) FROM poetry_types_zh_hans WHERE name=?", (fossil,)).fetchone()[0]
        check(n == 0, f"体裁形式表里没有「{fossil}」这个条目（旧模型化石）")

    # ---------------------------------------------------------------- D
    print("\nD. 【架构】体裁轴必须与朝代正交")
    if has_genres:
        print("     体裁 × 朝代 交叉表（简体）：")
        rows = con.execute("""
            SELECT g.name AS genre, count(DISTINCT p.dynasty_id) AS nd, count(*) AS n
            FROM poems_zh_hans p JOIN genres_zh_hans g ON g.id = p.genre_id
            GROUP BY g.id ORDER BY n DESC""").fetchall()
        for r in rows:
            note(f"{r['genre']:<6} {r['n']:>9,} 首 ｜ 跨 {r['nd']} 个朝代")

        # 哪些体裁**本来就**跨朝代：诗、词。
        # 曲（散曲）本就是元代的形式；诗经/楚辞/论语/四书五经各只有一部书，
        # 它们跨不了朝代是文学史事实，不是模型把朝代写死 —— 这里把两者区别点明。
        CROSS_DYNASTY = {"诗", "词"}
        for r in rows:
            if r["genre"] in CROSS_DYNASTY:
                check(r["nd"] >= 2, f"体裁「{r['genre']}」跨 ≥2 个朝代", f"实为 {r['nd']} 个")
            elif r["nd"] == 1:
                note(f"体裁「{r['genre']}」只在 1 个朝代 —— 文学史如此（元曲/诗经/楚辞…），非模型写死")

        # 「词」必须同时有宋和清 —— 纳兰性德的清词当年被错标成「宋词」
        ci = con.execute("""
            SELECT d.name AS dyn, count(*) n
            FROM poems_zh_hans p
            JOIN genres_zh_hans g ON g.id = p.genre_id
            JOIN dynasties_zh_hans d ON d.id = p.dynasty_id
            WHERE g.name='词' GROUP BY d.id ORDER BY n DESC""").fetchall()
        dyns = {r["dyn"] for r in ci}
        note("「词」分布：" + "、".join(f"{r['dyn']} {r['n']:,}" for r in ci[:6]))
        check({"宋", "清"} <= dyns, "「词」里同时有宋代和清代",
              "、".join(sorted(dyns)) if dyns else "空")
        # 李煜那类五代词
        if "五代" in dyns:
            note("五代词也在「词」里 ✓（旧模型单独开了个「五代词」，分类还和「宋词」不一致）")

        # 纳兰性德 —— 当年被错标成「宋词」的那个案例
        r = con.execute("""
            SELECT d.name AS dyn, g.name AS genre, count(*) n
            FROM poems_zh_hans p
            JOIN authors_zh_hans a ON a.id = p.author_id
            JOIN genres_zh_hans g ON g.id = p.genre_id
            JOIN dynasties_zh_hans d ON d.id = p.dynasty_id
            WHERE a.name LIKE '%纳兰%' GROUP BY d.id, g.id""").fetchall()
        if r:
            for x in r:
                note(f"纳兰性德：{x['dyn']} · {x['genre']} · {x['n']:,} 首")
            # 他既有词集也有诗集（《通志堂集》）—— 两边都对。
            # 当年错的是把他的清词标成「宋词」，不是「体裁判多了」。
            check(any(x["genre"] == "词" and x["dyn"] == "清" for x in r),
                  "纳兰性德（清）的词挂在「词」下（不是「宋词」）")
            check(all(x["dyn"] == "清" for x in r), "纳兰性德的朝代是「清」")

        # 诗也要跨朝代
        shi = con.execute("""
            SELECT count(DISTINCT p.dynasty_id) FROM poems_zh_hans p
            JOIN genres_zh_hans g ON g.id = p.genre_id WHERE g.name='诗'""").fetchone()[0]
        check(shi >= 2, "「诗」跨 ≥2 个朝代", f"{shi} 个")

    # ---------------------------------------------------------------- E
    print("\nE. 【架构】简繁两表 id 一一对应（同一 id = 同一首诗）")
    if "poems_zh_hant" in tables:
        nh = con.execute("SELECT count(*) FROM poems_zh_hans").fetchone()[0]
        nt = con.execute("SELECT count(*) FROM poems_zh_hant").fetchone()[0]
        note(f"简体 {nh:,} ｜ 繁体 {nt:,}")
        check(nh == nt, "两表行数相等", f"简 {nh:,} / 繁 {nt:,}")
        # 繁体表曾经自己去重丢行、又另开 id 计数器 → 两套 id 错开
        # （错到 #400000 时简《荒村》对繁《心雲詩爲羅宗仲先生賦》，
        #   GET /poems/400000?script=hant 会返回另一首诗）
        bad = con.execute("""
            SELECT count(*) FROM poems_zh_hans h
            LEFT JOIN poems_zh_hant t ON t.id = h.id
            WHERE t.id IS NULL
               OR h.author_id  IS NOT t.author_id
               OR h.dynasty_id IS NOT t.dynasty_id
               OR h.genre_id   IS NOT t.genre_id
               OR h.type_id    IS NOT t.type_id
               OR h.source     IS NOT t.source""").fetchone()[0]
        check(bad == 0, "同 id 的元数据（作者/朝代/体裁/形式/来源）完全一致", f"不符 {bad:,}")
        # 抽查标题：**繁体标题转简体后必须等于简体标题**（简体表是归一化的一侧）。
        # 反方向（简体转繁）不要求字字相等 —— 来源本身就是繁体时，
        # 繁体表有意保留祖本的**异体字**（劒/劍、棊/棋、綵/彩、製/制、迴/回、歎/嘆），
        # 那是忠实原文，不是错误。实测 800 条里反方向有 24 条"不符"，全是这类异体字。
        try:
            import zhconv
            # 全库等距抽样：LIMIT 800 只会落在 id 1..800（全在 chinese-poetry 段），
            # 抽样必须跨全库才有意义
            step = max(1, nh // 4000)
            n_ok = n_bad = 0
            bad_ex = []
            for r in con.execute(
                    "SELECT h.id, h.title AS ht, t.title AS tt FROM poems_zh_hans h "
                    "JOIN poems_zh_hant t ON t.id = h.id "
                    "WHERE h.id % ? = 0 LIMIT 4000", (step,)):
                if zhconv.convert(r["tt"] or "", "zh-hans") == (r["ht"] or ""):
                    n_ok += 1
                else:
                    n_bad += 1
                    if len(bad_ex) < 3:
                        bad_ex.append(f"#{r['id']} 简《{r['ht']}》/ 繁《{r['tt']}》")
            tot = n_ok + n_bad
            note(f"标题一致性抽查：{n_ok}/{tot} 相符（{n_ok * 100.0 / tot:.1f}%）")
            for e in bad_ex:
                note(f"  不符样例：{e}")
            # 残余不符全属一类：zhconv **两个方向都不归一**的异体字
            # （馀/餘、鍊/炼、槩/槪、劒/劍、棊/棋…），祖本用哪个就留哪个。
            # 两表仍是同一首诗（id、作者、内容都对得上），所以按 ≥99% 断言 ——
            # 真要错位（如 v0.2.0 那套独立计数器），这里会立刻掉到很低。
            check(n_bad * 100 <= tot, "标题在两种写法下 ≥99% 指向同一首诗")
        except ImportError:
            note("没装 zhconv，跳过标题抽查")

    # ---------------------------------------------------------------- F
    print(f"\nF. content_hash 抽样重算（{a.sample} 条）")
    step = max(1, con.execute("SELECT count(*) FROM poems_zh_hans").fetchone()[0] // a.sample)
    bad = 0
    checked = 0
    for r in con.execute(
            "SELECT id, content, content_hash FROM poems_zh_hans "
            "WHERE id % ? = 0 LIMIT ?", (step, a.sample)):
        try:
            paras = json.loads(r["content"])
        except Exception:
            bad += 1
            continue
        checked += 1
        if content_hash([str(p) for p in paras]) != r["content_hash"]:
            bad += 1
            if bad <= 3:
                note(f"哈希不符：#{r['id']} {r['content_hash'][:12]}…")
    check(bad == 0, "抽样哈希全部重算一致", f"抽 {checked:,} 条，不符 {bad}")

    # ---------------------------------------------------------------- 结论
    print("\n" + "─" * 62)
    if FAILS:
        print(f"❌ 有 {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   - {f}")
        sys.exit(1)
    print("✅ 全部通过 —— 架构与数据完整性 OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
