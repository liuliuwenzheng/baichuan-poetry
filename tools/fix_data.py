#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据修复脚本 —— 修正本地 poetry.db 的两类上游问题。

① 朝代归位：上游导入把「全宋诗」整批灌进了「唐」（authors 表是对的，poems 表全错）。
   用上游权威清单（全唐诗/authors.song.json 等）重判作者朝代，再让诗跟随作者。
   → 影响约 14 万首（38%）。

② 简繁污染：上游用 OpenCC s2twp（简体→台湾正体）做了转换，短语误匹配把
   「海内存知己」的「内存」当成「记忆体」替换了 → 「海记忆体知己」。
   修正并重算 content_hash（= sha256(段落直接拼接)）。

安全：默认 dry-run；--apply 才写库，并写 data/fix_log.json 保存原值以便回滚。

  python fix_data.py                 # 干跑，只看影响面
  python fix_data.py --apply         # 实际写入
  python fix_data.py --rollback      # 用 fix_log.json 回滚
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
try:
    from poem import _data_file                         # noqa: E402
except ImportError:                                     # 独立运行（不依赖查询 CLI）
    def _data_file(name):
        return os.path.join(HERE, "..", "data", name)

try:
    from zhconv import convert as _convert              # 简繁归一化匹配用
except ImportError:
    _convert = None

D_SONG, D_TANG, D_WUDAI = 8, 6, 7
LOGFILE = os.path.normpath(os.path.join(HERE, "..", "data", "fix_log.json"))

# 经人工核对确认的简繁污染（其余「资讯/飞弹/程式/档案/透过」等是合法古典用法，不动）
CORRUPT = {
    "zh_hans": [("记忆体", "内")],
    "zh_hant": [("記憶體", "內")],
}


def content_hash(paras):
    """与上游一致的算法：sha256(段落直接拼接)"""
    return hashlib.sha256("".join(paras).encode("utf-8")).hexdigest()


def load_lists():
    p = os.path.join(HERE, "dynasty_lists.json")        # 随 tools/ 一起分发
    if not os.path.exists(p):
        p = os.path.normpath(os.path.join(HERE, "..", "data", "dynasty_lists.json"))
    if not os.path.exists(p):
        sys.exit(f"缺少 {p}（上游权威作者清单）")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def _simp(s):
    """归一化匹配键：简繁统一到简体再比。库里的繁体名是 陸遊/楊萬裏，
    而上游清单里是 陆游/杨万里——不归一化就整批对不上。"""
    if _convert is None:
        return s
    return _convert(s, "zh-hans")


def plan_dynasty(con, lang, songs, tangs, wudai=frozenset()):
    """返回 (需改的宋作者, 需改的五代作者, 歧义数, 清单外数, 总数)

    只做「有权威清单背书」的判断，且顺序固定为「确定 > 一般」：
      南唐二主 → 五代（最硬的事实，优先于一切；李煜同时在宋/唐两表里）
      在宋清单且不在唐清单 → 宋
      在唐清单且不在宋清单 → 唐
      两表都有（五代跨代/无名氏/佚名）→ 不动
    传入的 songs/tangs/wudai 已归一为简体。
    """
    rows = con.execute(f"SELECT id, name, dynasty_id FROM authors_{lang}").fetchall()
    to_song, to_tang, to_wudai, amb, keep = [], [], [], 0, 0
    for aid, name, dyn in rows:
        key = _simp(name)
        ins, int_, inw = key in songs, key in tangs, key in wudai
        if inw:                                     # 南唐二主：确定，先判
            if dyn != D_WUDAI:
                to_wudai.append([D_WUDAI, aid, dyn, name])
            continue
        if ins and int_:
            amb += 1
            continue
        if ins:
            if dyn != D_SONG:
                to_song.append([D_SONG, aid, dyn, name])
        elif int_:
            if dyn != D_TANG:
                to_tang.append([D_TANG, aid, dyn, name])
        else:
            keep += 1
    return to_song, to_tang, to_wudai, amb, keep, len(rows)


def plan_content(con, lang, pairs):
    """返回 [(poem_id, 新content, 新hash, 旧content, 旧hash, 替换说明)]"""
    upd = []
    for bad, good in pairs:
        for pid, content, chash in con.execute(
                f"SELECT id, content, content_hash FROM poems_{lang} WHERE content LIKE ?",
                (f"%{bad}%",)).fetchall():
            paras = json.loads(content)
            new = [p.replace(bad, good) for p in paras]
            if new == paras:
                continue
            upd.append([pid, json.dumps(new, ensure_ascii=False), content_hash(new),
                        content, chash, f"{bad}→{good}"])
    return upd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写入")
    ap.add_argument("--rollback", action="store_true", help="用 fix_log.json 回滚")
    ap.add_argument("--db", default=None)
    a = ap.parse_args()

    db = a.db or _data_file("poetry.db")
    if not os.path.exists(db):
        sys.exit(f"找不到数据库：{db}\n"
                 f"  这是 chinese-poetry（诗泉）发布的 poetry.db，请先下载解压到 data/，\n"
                 f"  或用 --db 指定路径。")
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM poems_zh_hans LIMIT 1")
    except sqlite3.OperationalError:
        sys.exit(f"{db} 不是有效的诗库（缺少 poems_zh_hans 表）——请确认下载的是 poetry.db")

    # ---------------- 回滚 ----------------
    if a.rollback:
        with open(LOGFILE, encoding="utf-8") as f:
            log = json.load(f)
        n = 0
        for lang, items in log.get("authors", {}).items():
            con.executemany(f"UPDATE authors_{lang} SET dynasty_id=? WHERE id=?",
                            [(old, aid) for _, aid, old, _ in items])
            n += len(items)
        for lang, items in log.get("poems_dyn", {}).items():
            con.executemany(f"UPDATE poems_{lang} SET dynasty_id=? WHERE id=?",
                            [(old, aid) for _, aid, old, _ in items])
            n += len(items)
        for lang, items in log.get("poems_content", {}).items():
            con.executemany(f"UPDATE poems_{lang} SET content=?, content_hash=? WHERE id=?",
                            [(oc, oh, pid) for pid, _, _, oc, oh, _ in items])
            n += len(items)
        con.commit()
        print(f"已回滚 {n} 条")
        return

    lists = load_lists()
    songs = set(lists["song"])
    tangs = set(lists["tang"])
    wudai_raw = set(lists.get("nantang", []))

    log = {"authors": {}, "poems_dyn": {}, "poems_content": {}}
    total_a = total_p = total_c = 0

    for lang, conv in (("zh_hans", "hans"), ("zh_hant", "hant")):
        # 两边都归一化到简体再比：清单里存的是简体名（陆游/杨万里），
        # 而繁体库里是繁体名（陸遊/楊萬裏）——不归一化繁体库整批对不上
        if _convert is None:
            sys.exit("需要 zhconv：pip install zhconv")
        S = {_simp(n) for n in songs}
        T = {_simp(n) for n in tangs}
        W = {_simp(n) for n in wudai_raw}

        to_song, to_tang, to_wudai, amb, keep, tot = plan_dynasty(con, lang, S, T, W)
        au = to_song + to_tang + to_wudai
        print(f"\n=== {lang} 作者表（共 {tot} 位）===")
        print(f"  改判为宋 {len(to_song)} ｜ 改判为唐 {len(to_tang)} ｜ 改判为五代 {len(to_wudai)}"
              f" ｜ 合计需改 {len(au)}")
        print(f"  未动：宋唐两表重叠 {amb}（五代跨代/无名氏/佚名）｜ 清单外 {keep}")

        before = {}
        if a.apply:
            cur = con.execute(f"""SELECT d.name, count(*) FROM poems_{lang} p
                JOIN dynasties_{lang} d ON p.dynasty_id=d.id GROUP BY d.name""")
            before = dict(cur.fetchall())

        if a.apply:
            con.executemany(f"UPDATE authors_{lang} SET dynasty_id=? WHERE id=?",
                            [(t, aid) for t, aid, _, _ in au])
            # 保守传播：只修「诗标为唐」这一种已知的默认错误。
            # 诗标为元/五代/先秦/宋 的都来自各自正确的源，一律不动
            # （否则会把无名氏的元曲改成宋、把花间集改成唐、把诗经改成其他）。
            ids = [r[0] for r in con.execute(
                f"""SELECT p.id FROM poems_{lang} p JOIN authors_{lang} a ON p.author_id=a.id
                    WHERE p.dynasty_id=? AND a.dynasty_id NOT IN (?, 11)""",
                (D_TANG, D_TANG))]
            # 单条 SQL 批量更新（逐行 executemany 在这个量级要跑好几分钟）
            con.execute(
                f"""UPDATE poems_{lang} SET dynasty_id=(
                        SELECT a.dynasty_id FROM authors_{lang} a
                        WHERE a.id=poems_{lang}.author_id)
                    WHERE dynasty_id=? AND author_id IN (
                        SELECT id FROM authors_{lang} WHERE dynasty_id NOT IN (?, 11))""",
                (D_TANG, D_TANG))
            log["authors"][lang] = au
            log["poems_dyn"][lang] = [[D_TANG, i, D_TANG, ""] for i in ids]
            total_p += len(ids)
            print(f"  诗表已改 {len(ids):,} 首")

        if a.apply:
            after = dict(con.execute(f"""SELECT d.name, count(*) FROM poems_{lang} p
                JOIN dynasties_{lang} d ON p.dynasty_id=d.id GROUP BY d.name""").fetchall())
            print(f"  诗表朝代分布（本表 {len(ids):,} 首改动）：")
            for k in sorted(set(before) | set(after), key=lambda x: -after.get(x, 0)):
                b, af = before.get(k, 0), after.get(k, 0)
                print(f"    {k:<6}{af:>9,}" + (f"   ← {b:,}" if b != af else ""))

        # 内容修复
        cu = plan_content(con, lang, CORRUPT[lang])
        print(f"=== {lang} 内容污染 ===")
        for pid, _, _, _, _, note in cu:
            print(f"  #{pid}  {note}")
        if a.apply and cu:
            con.executemany(f"UPDATE poems_{lang} SET content=?, content_hash=? WHERE id=?",
                            [(nc, nh, pid) for pid, nc, nh, _, _, _ in cu])
            log["poems_content"][lang] = cu
        total_c += len(cu)
        total_a += len(au)

    if a.apply:
        # 诗的朝代改动这里没逐条记录（14 万条），回滚用「按作者重算」即可
        with open(LOGFILE, "w", encoding="utf-8") as f:
            json.dump(log, f, ensure_ascii=False, indent=1)
        con.commit()
        print(f"\n✅ 已写入：作者朝代 {total_a} 条、诗朝代 {total_p} 条、内容 {total_c} 条")
        print(f"   回滚日志 → {LOGFILE}")
        print("   校验：" + con.execute("PRAGMA quick_check").fetchone()[0])
    else:
        print(f"\n（干跑）将改：作者朝代 {total_a} 条、内容 {total_c} 条"
              f"（诗朝代随作者自动跟随，约 14 万条）")
        print("   确认无误后加 --apply 写入")


if __name__ == "__main__":
    main()
