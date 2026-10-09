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

CREATE TABLE poems_zh_hans (                -- 繁体表是它的**同 id 镜像**，见第七节
  id INTEGER PRIMARY KEY, type_id INTEGER, genre_id INTEGER, tune TEXT,
  title TEXT, content TEXT,
  content_hash TEXT, author_id INTEGER, dynasty_id INTEGER, created_at TEXT,
  source TEXT,            -- 【新增】数据来源
  period_orig TEXT,       -- 【新增】上游原始朝代标签（'元末明初'『清末民国初』…）
  lang_original TEXT      -- 【新增】'zh-Hans' | 'zh-Hant'
);
-- content 为 JSON 数组：["城阙辅三秦，风烟望五津。", "与君离别意，同是宦游人。"]
-- content_hash = sha256(段落直接拼接)，与上游算法一致（去重、校验用）
-- genre_id 【v0.3.0 新增】体裁轴 → genres_*，**与朝代正交**
-- tune     【v0.3.0 新增】词牌/曲牌（「水调歌头」「念奴娇」），只有词曲才有

CREATE TABLE poetry_types_zh_hans (         -- 形式：五言绝句 / 七言律诗 / 古体…
  id INTEGER PRIMARY KEY, name TEXT, category TEXT,
  lines INTEGER, chars_per_line INTEGER, description TEXT, created_at TEXT
);
-- ⚠️ v0.3.0 起名字里**不带朝代**：
--    10「唐诗」→ 10「诗」；20「宋词」→ 20「词」；21「五代词」并入 20「词」
--    理由见第六节（朝代必须归 dynasty_id）

CREATE TABLE genres_zh_hans (               -- 【v0.3.0 新增】体裁轴，与朝代正交
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, name_en TEXT,
  description TEXT, created_at TEXT
);
-- 1诗 2词 3曲 4诗经 5楚辞 6论语 7蒙学 8四书五经 9其他
-- 繁体表 genres_zh_hant 同构
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
CREATE INDEX idx_hans_author ON poems_zh_hans(author_id);
CREATE INDEX idx_hans_dyn    ON poems_zh_hans(dynasty_id);
CREATE INDEX idx_hans_src    ON poems_zh_hans(source);
CREATE INDEX idx_hans_hash   ON poems_zh_hans(content_hash);   -- 非唯一！上游有意保留「重出诗」
-- v0.3.0 新增：接口最常用的两类查询
CREATE INDEX idx_hans_genre     ON poems_zh_hans(genre_id);              -- 「所有词，不分朝代」
CREATE INDEX idx_hans_genre_dyn ON poems_zh_hans(genre_id, dynasty_id);  -- 「唐诗」「宋词」
CREATE INDEX idx_hans_tune      ON poems_zh_hans(tune);                  -- 「所有《水调歌头》」
-- 繁体表同名 *hant 同构；poem_texts 另有 idx_texts_lang(poem_kind, poem_id)

CREATE VIRTUAL TABLE poems_fts_zh_hans USING fts5(title, content, ...);
```

> 两个索引的存在就是为了「朝代 × 体裁」这一对组合：查「唐诗」= `genre_id=1 AND dynasty_id=6`，
> 查「宋词」= `genre_id=2 AND dynasty_id=8`，查「所有词」= 只用 `genre_id`。一个组合索引全包。

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

## 七、简繁两表必须**同 id 镜像**（v0.3.0 修正的真实 bug）

第六节说简繁分开能零成本复用工具链，但**前提是两表 id 对齐** —— 否则
`GET /poems/{id}` 换一个文字版本就返回另一首诗，这是接口层的致命伤。

v0.2.0 犯的错：繁体表自己又做了一次去重（`if hant_hash not in seen_hant`），
还配了独立的 `next_pid_h` 计数器。于是繁体侧每丢一行，两套 id 就错开一位，
**越往后错得越多**：

| id | 简体表 | 繁体表 | |
|---|---|---|---|
| 11 | 《饮马长城窟行》 | 《飲馬長城窟行》 | ✅ 同一首 |
| 200000 | 《题赵苇江雁荡图》 | 《題趙葦江雁蕩圖》 | ✅ 同一首 |
| 400000 | 《荒村》 | 《心雲詩爲羅宗仲先生賦》 | ❌ **不同的诗** |
| 960000 | 《秋日书怀…其二》 | 《陽春門堤上》 | ❌ **不同的诗** |

**修正后的不变量**（`tools/verify.py` 的 E 段自动守）：

1. **诗的身份只由简体表定义**，去重只发生在简体侧；
2. 繁体表是简体表的**同 id 逐条镜像**，不设独立计数器、不做二次去重；
3. 两表行数恒等；同 id 的 `author_id / dynasty_id / genre_id / type_id / source` 完全一致；
4. 同一 id 的标题必然是同一首诗的简繁两种写法。

繁体侧不再去重，意味着**繁体文本里可能出现字面重复**（两首不同的诗转成繁体后字面相同）。
这是对的：诗的身份不由繁体写法决定，把这两首诗合并才是真的丢数据。

## 八、为什么体裁轴独立成表，而不复用 `category` 字段

`poetry_types.category` 看起来能当体裁用（旧模型就写了 `category='唐诗'`），
但它是个**自由文本描述字段**：不参与外键、不能索引、随手就能改坏。

真正的体裁轴要满足三件事：① 与朝代无关；② 有稳定 id 供接口过滤；
③ 能被外键约束 + 校验脚本守住完整性。所以给它一张真表 `genres_*`，
`poems.genre_id` 挂上去，再由 `tools/verify.py` 断言「体裁名里不得出现朝代名」。

