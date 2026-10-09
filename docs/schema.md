# 数据库 Schema 设计

设计目标：**多源**（chinese-poetry / Werneror / 未来各国诗集）、**多语言**
（中文诗的简繁两版 + 英译；外国诗的原文 + 中文 + 英文），同时**不破坏现有工具**
（`poem.py` / `peishi.py` / `build_index.py` 直接 `--db data/baichuan.db` 即可用）。

原则：中文诗表**沿用上游 poetry.db 的表名与字段**（`poems_zh_hans` 等）并追加列，
而不是另起炉灶——这样上游的 37 万首和新增的 85 万首共用一套查询代码。

---

## 一、中文诗（沿用上游表名 + 追加列）

```sql
CREATE TABLE dynasties_zh_hans (            -- 繁体表同构 _zh_hant
  id INTEGER PRIMARY KEY, name TEXT, name_en TEXT,
  start_year INTEGER, end_year INTEGER, created_at TEXT
);
-- 上游 11 个：1先秦 2两汉 3魏晋 4南北朝 5隋 6唐 7五代 8宋 9元 10清 11其他
-- 百川追加：12秦 13辽 14金 15明 16民国 17近现代 18当代

CREATE TABLE authors_zh_hans (
  id INTEGER PRIMARY KEY, name TEXT, dynasty_id INTEGER,
  description TEXT, created_at TEXT,
  source TEXT,            -- 【新增】'chinese-poetry' | 'werneror' | …
  name_en TEXT,           -- 【新增】拼音/英文名
  name_orig TEXT          -- 【新增】原文姓名（为未来非中文诗人预留）
);

CREATE TABLE poems_zh_hans (                -- 繁体表同构 _zh_hant
  id INTEGER PRIMARY KEY, type_id INTEGER, title TEXT, content TEXT,
  content_hash TEXT, author_id INTEGER, dynasty_id INTEGER, created_at TEXT,
  source TEXT,            -- 【新增】数据来源
  period_orig TEXT,       -- 【新增】上游原始朝代标签（'元末明初'『清末民国初』…）
  lang_original TEXT      -- 【新增】'zh-Hans' | 'zh-Hant'
);
-- content 为 JSON 数组：["城阙辅三秦，风烟望五津。", "与君离别意，同是宦游人。"]
-- content_hash = sha256(段落直接拼接)，与上游算法一致（去重、校验用）

CREATE TABLE poetry_types_zh_hans (id INTEGER PRIMARY KEY, name TEXT, description TEXT);
```

## 二、译文（语言的横轴）

一张表同时解决两个方向：**中文诗加英译**、**外国诗加中译**。

```sql
CREATE TABLE poem_texts (
  id INTEGER PRIMARY KEY,
  poem_kind TEXT NOT NULL,   -- 'zh_hans' | 'zh_hant' | 'world'
  poem_id   INTEGER NOT NULL,
  lang      TEXT NOT NULL,   -- BCP-47：en / zh-Hans / ja / fa / ru …
  title     TEXT,
  content   TEXT,            -- JSON 数组，与 content 同构
  translator TEXT,
  source    TEXT,
  UNIQUE(poem_kind, poem_id, lang)
);
```

## 三、世界诗歌（非中文原文）

```sql
CREATE TABLE authors_world (
  id INTEGER PRIMARY KEY,
  name_orig TEXT NOT NULL,   -- 原文姓名（Rumi / 松尾芭蕉 / Pushkin）
  name_zh   TEXT, name_en TEXT,
  country   TEXT,            -- ISO 3166
  lang_original TEXT,        -- fa / ja / ru / en
  birth_year INTEGER, death_year INTEGER
);

CREATE TABLE poems_world (
  id INTEGER PRIMARY KEY,
  lang_original TEXT NOT NULL,
  title_orig TEXT, title_zh TEXT, title_en TEXT,
  content_orig TEXT NOT NULL,     -- JSON 数组
  author_id INTEGER REFERENCES authors_world(id),
  period_orig TEXT, year_start INTEGER, year_end INTEGER,
  source TEXT
);
```

## 四、索引

```sql
CREATE INDEX idx_poems_author   ON poems_zh_hans(author_id);
CREATE INDEX idx_poems_dynasty  ON poems_zh_hans(dynasty_id);
CREATE INDEX idx_poems_source   ON poems_zh_hans(source);
CREATE UNIQUE INDEX idx_poems_hash ON poems_zh_hans(content_hash);   -- 跨源去重
CREATE VIRTUAL TABLE poems_fts USING fts5(title, content, content=poems_zh_hans, ...);
```

## 五、上游标签 → 百川朝代 id

Werneror 的朝代标签更细，保留在 `period_orig`，同时归一到 `dynasty_id`：

| 上游标签 | dynasty_id | | 上游标签 | dynasty_id |
|---|---|---|---|---|
| 先秦 | 1 | | 辽 | 13 |
| 秦 | 12 | | 金、金末元初 | 14 |
| 汉 | 2 | | 元、元末明初 | 9 |
| 魏晋、魏晋末南北朝初 | 3 | | 明、明末清初 | 15 |
| 南北朝 | 4 | | 清、清末民国初、清末近现代初 | 10 |
| 隋、隋末唐初 | 5 | | 民国末当代初 | 16 |
| 唐、唐末宋初 | 6 | | 近现代、近现代末当代初 | 17 |
| 五代 | 7 | | 当代 | 18 |
| 宋、宋末元初、宋末金初 | 8 | | | |

## 六、为什么不用「单一 poems 表 + lang 列」

理论上最干净，但会让现有查询工具（`poem.py` 的 `poems_{lang}` 拼接、FTS 表名、
倒排索引）全部失效。中文简繁两库本来就是上游的物理分区，保留它能**零成本复用**
全部已有工具链（意象检索、作者画像、观点配诗），把多语言能力放在 `poem_texts`
和 `poems_world` 上，是收益最高的折中。
