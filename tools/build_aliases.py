#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
别名库构建器 —— 让人能用「习惯叫法」查到诗。

问题：数据源用的是诗人的正式名（陶潜、郑燮、李煜），而读者搜的是习惯名
（陶渊明、郑板桥、李后主）。不解决，等于一半的诗对普通人不可见。

做法：可核对的人工种子表（本名→别名），只收录**该诗人在库中确实存在**的条目，
写进 author_aliases 表；查询时 alias 与 name 等价。

用法：
  python tools/build_aliases.py            # 建表并写入
  python tools/build_aliases.py --check    # 只看命中/未命中
"""
import argparse
import os
import sqlite3
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "data", "baichuan.db")

# {库中本名: [别名…]} —— 全部是史有定论的「字/号/别称」，不用机器猜
SEED = {
    "陶潜": ["陶渊明", "陶淵明", "陶靖节", "陶靖節", "五柳先生"],
    "李白": ["李太白", "青莲居士", "青蓮居士", "诗仙", "詩仙"],
    "杜甫": ["杜工部", "杜少陵", "杜子美", "诗圣", "詩聖"],
    "白居易": ["白乐天", "白樂天", "香山居士", "白香山"],
    "苏轼": ["苏东坡", "蘇東坡", "苏子瞻", "蘇子瞻", "东坡居士", "蘇軾"],
    "王维": ["王摩诘", "王摩詰", "王右丞"],
    "李商隐": ["李義山", "李义山", "玉溪生"],
    "刘禹锡": ["刘梦得", "劉夢得"],
    "柳宗元": ["柳河东", "柳河東", "柳子厚"],
    "韩愈": ["韩昌黎", "韓昌黎", "韩退之", "韓退之"],
    "陆游": ["陆放翁", "陸放翁", "陸遊"],
    "辛弃疾": ["辛稼轩", "辛稼軒"],
    "李清照": ["李易安", "易安居士"],
    "李煜": ["李后主", "李後主", "南唐后主", "南唐後主"],
    "温庭筠": ["温飞卿", "溫飛卿"],
    "韦庄": ["韦端己", "韋端己"],
    "孟浩然": ["孟襄阳", "孟襄陽"],
    "岑参": ["岑嘉州", "岑參"],
    "高适": ["高常侍", "高適"],
    "杜牧": ["杜牧之", "杜樊川"],
    "谢灵运": ["谢康乐", "謝康樂"],
    "曹植": ["曹子建"],
    "嵇康": ["嵇叔夜"],
    "阮籍": ["阮嗣宗"],
    "鲍照": ["鲍参军", "鮑參軍"],
    "庾信": ["庾子山"],
    "郑燮": ["郑板桥", "鄭板橋", "郑克柔"],
    "龚自珍": ["龚定庵", "龔定庵", "龚定盦"],
    "纳兰性德": ["纳兰容若", "納蘭容若", "纳兰成德"],
    "唐寅": ["唐伯虎", "唐六如"],
    "袁枚": ["袁子才", "随园主人", "隨園主人"],
    "赵翼": ["赵瓯北", "趙甌北"],
    "高启": ["高青丘", "高季迪"],
}
KIND = {"字": "字", "号": "号"}

DDL = """
CREATE TABLE IF NOT EXISTS author_aliases (
  alias     TEXT NOT NULL,
  author_id INTEGER NOT NULL,
  lang      TEXT NOT NULL,        -- 'zh_hans' | 'zh_hant'
  kind      TEXT,                 -- 本名 / 字 / 号 / 别称
  PRIMARY KEY (alias, author_id, lang)
);
CREATE INDEX IF NOT EXISTS idx_alias_alias ON author_aliases(alias);
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DB)
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()

    con = sqlite3.connect(a.db)
    con.executescript(DDL)
    try:
        from zhconv import convert
    except ImportError:
        sys.exit("需要 zhconv：pip install zhconv")

    # 用简繁归一化建索引：库里有 陸遊 也有 陆游，一个本名要能同时找到两边
    simp = lambda s: convert(s, "zh-hans")
    by_simp = {}
    for lang in ("zh_hans", "zh_hant"):
        for aid, nm in con.execute(f"SELECT id,name FROM authors_{lang}"):
            by_simp.setdefault((lang, simp(nm)), aid)

    rows, hit, miss = [], 0, 0
    for name, aliases in SEED.items():
        found = False
        for lang in ("zh_hans", "zh_hant"):
            aid = by_simp.get((lang, simp(name)))
            if aid is None:
                continue
            found = True
            nb = con.execute(f"SELECT name FROM authors_{lang} WHERE id=?", (aid,)).fetchone()[0]
            rows.append((nb, aid, lang, "本名"))
            for al in aliases:
                rows.append((al, aid, lang, "别称"))
        hit, miss = (hit + 1, miss) if found else (hit, miss + 1)
        if not found:
            print(f"  ⚠️ 库中找不到本名「{name}」，其别名未收录")
    con.executemany("INSERT OR IGNORE INTO author_aliases VALUES (?,?,?,?)", rows)
    con.commit()
    n = con.execute("SELECT count(*) FROM author_aliases").fetchone()[0]
    print(f"✅ author_aliases 共 {n} 条（本名 {len(SEED)} 组，命中 {hit}，未命中 {miss}）")
    if a.check:
        for lang in ("zh_hans", "zh_hant"):
            print(f"  [{lang}]")
            for al, nb in con.execute("""SELECT a.alias, b.name FROM author_aliases a
                JOIN authors_""" + lang + """ b ON a.author_id=b.id
                WHERE a.lang=? AND a.alias <> b.name ORDER BY a.alias LIMIT 8""", (lang,)):
                print(f"    「{al}」→ {nb}")


if __name__ == "__main__":
    main()
