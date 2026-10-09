# Release 说明 · 数据包

数据库不入 git（1.1 GB，超过 GitHub 单文件 100 MB 限制），以 **Release 附件** 发布。

---

## v0.4.0 —— 可查可用版（2026-10-09）

**数据一行没变**（简体 993,753 / 繁体 993,753，同 id 镜像）。这一版补的是
**两样 v0.3.0 漏掉的工具链产物**，以及由它们暴露出来的**三个真 bug**。

### 补了什么

| | v0.3.0 | **v0.4.0** |
|---|---|---|
| 别名表 `author_aliases` | ❌ 走 `--cp-json` 时没建 → 「陶渊明」查不到「陶潜」（其实有 151 首） | ✅ 240 条 / 33 组（本名·字·号·别称） |
| 全文检索（FTS5） | ❌ 没建 → `poem.py search` 直接报 `no such table: poems_fts_zh_hans` | ✅ `tools/build_search.py`，**可选**；缺了自动降级 |

### 修了三个真 bug

1. **库没建索引时 `search` 直接崩** → 改为能力探测 + 自动退化成子串扫描，
   并**说明为什么**。降级路径必须在输出里可见，不能悄悄发生。
2. **别名表建了，但 CLI 压根没用它** —— `random --author 陶渊明` **静默返回空**。
   用户看到空输出，结论会是「库里没有陶渊明」。现在 `random` / `author` / `astat`
   三个入口都走别名（陶渊明→陶潜、李后主→李煜、苏东坡→苏轼、唐伯虎→唐寅、
   郑板桥→郑燮、五柳先生→陶潜），**查不到会明确报错并给提示**，不再空手而归。
3. **「搜不到」和「不存在」被混为一谈** —— 上面两条的根因都是它。

### 为什么不把全文索引打进数据包

索引是**派生产物**，一条命令就能重建。打进包会让下载从 **481 MB 涨到 1.86 GB** ——
而**多下的这 1.4 GB，比在本地重建（15 分钟）还费时间**。数据包只装数据。

```bash
# 想要快检索就本地建（约 15 分钟，库会大 2 GB；两条 SQL 的 B+ 树，无法再小）
python tools/build_search.py
```

不建也完全能用：CLI 与开放接口都会**自动退回子串扫描**（结果完整，约 0.3 秒），
并在输出/响应里如实说明走了哪条路。

### 构建命令（可复现）

```bash
python tools/ingest.py --cp-json <chinese-poetry 源码> --werneror <Werneror CSV 目录> \
                       --out data/baichuan.db --to-hant        # 119s
python tools/build_aliases.py --db data/baichuan.db            # 240 条
python tools/verify.py --db data/baichuan.db                   # 26 条断言，全绿
python tools/build_search.py                                   # 可选：全文索引
```

### 附件（v0.4.0）

| 文件 | 大小 | sha256 |
|---|---|---|
| `baichuan.db.gz` | 481,432,326 B（原库 1,196,511,232 B，压缩比 2.49:1） | `aae8c1b08e265b056edb1d6b15594549a0ceb050268f92942faa17f7005d3db5` |

解压后 `baichuan.db` 的 sha256 应为
`b845d15dd0223d5196b75170c181b82170a77928d56e0d442da7495594c178e8`

> 这个包**不含**全文索引。用接口测试跑过一遍：57 条断言全绿（4 条 FTS 专属的自动跳过），
> `capabilities` 如实报告 `全文检索=[]`，2 字与 3 字检索都走降级路径且结果正确。
> —— **发出去的包必须自己先跑一遍**，不能假设。

### 接口在诗文树

REST + GraphQL + Python SDK 在 **[诗文树 poetry-tree](https://github.com/liuliuwenzheng/poetry-tree)**。
百川是它的数据源适配器；接口只读，且启动时会审计库里的来源授权。

---

## v0.3.0 —— 架构正交版（2026-10-09）

**这一版数据条数没变，修的是「模型」：两个维度被压成一维、两套 id 各自为政。**

| 病灶 | v0.2.0 | **v0.3.0** |
|---|---|---|
| 体裁与朝代 | 体裁名写成「唐诗」「宋词」「五代词」，两个维度压进一个字段 | **独立 `genres_*` 体裁轴**，与 `dynasty_id` 正交可组合 |
| 清词归类 | 纳兰性德 257 首清词挂在「宋词」下 | 归「词」，朝代 = 清 |
| 查「所有词」 | 硬编码 `type_id IN (20,21)`，来新朝代会崩 | `genre_id = 2`，一行搞定 |
| 词牌/曲牌 | 没有这一列 | `tune` 列：词 42,254 首（99.4%）、曲 8,698 首（79.9%） |
| 简繁两表 id | 各自开计数器 → **错位 25,294 个**（#400000 简《荒村》对繁《心雲詩…》） | **同 id 镜像**，行数恒等 993,753 / 993,753 |
| 繁体库行数 | 968,459（比简体少 25,294） | 993,753（与简体恒等） |
| 自动校验 | 无 | **`tools/verify.py`**：26 条断言，含「体裁名不得含朝代名」「两表 id 一一对应」 |

`type_id` 名字同时去朝代化：10「唐诗」→ 10「诗」，20「宋词」→ 20「词」，21「五代词」并入 20。

### 正交之后能查什么

```bash
python query/poem.py --db data/baichuan.db genres            # 看体裁轴
python query/poem.py --db data/baichuan.db random --genre 词              # 所有词，不分朝代
python query/poem.py --db data/baichuan.db random --genre 诗 --dynasty 唐  # 唐诗
python query/poem.py --db data/baichuan.db random --genre 词 --dynasty 清  # 清词
```

| 组合 | 数量 |
|---|---|
| 诗 | 939,928（跨 17 个朝代） |
| 词 | 42,511（跨 13 个朝代） |
| 唐诗 / 宋诗 | 88,507 / 366,750 |
| 宋词 / 清词 | 23,362 / 8,579 |
| 《水调歌头》全库 | 宋 789 / 近现代 78 / 清 68 / 元 56 / 金 11 / 当代 30 … |

### 构建命令（可复现）

```bash
git clone --depth 1 https://github.com/chinese-poetry/chinese-poetry.git
git clone --depth 1 https://github.com/Werneror/Poetry.git
python tools/ingest.py --cp-json ./chinese-poetry \
  --werneror ./Poetry --out data/baichuan.db --to-hant   # 约 125s
python tools/verify.py                                   # 26 条断言，全绿才算过
```

### 附件（v0.3.0）

| 文件 | 大小 | sha256 |
|---|---|---|
| `baichuan.db.gz` | 476,093,085 B（原库 1,196,482,560 B，压缩比 2.51:1） | `ca84c3e03d64037439c1848b73932946607222c6ede8e074c0b56f2f25af1ade` |

解压后 `baichuan.db` 的 sha256 应为
`22e96f1a19a9781cb169b7dcfc49dfad57c108e9a115dbf8ce17056fe224a8e0`

---

## v0.2.0 —— 授权彻底干净版（2026-10-09）

**这一版最重要的变化不是数据变多，而是授权链彻底干净了。**

| | v0.1.0 | **v0.2.0** |
|---|---|---|
| 唐宋来源 | `palemoky/chinese-poetry-api`（**GPL-3.0**）的 `poetry.db` 产物 | **chinese-poetry 原始 JSON（MIT）** |
| GPL 产物 | ⚠️ 含 | ✅ **完全不含** |
| 朝代判定 | 导入后事后修补 25 万首 | **导入时按文件名判**，从源头不错 |
| 上游错字 | 未修 | 勘误表修掉「海記憶體」 |

**v0.2.0 构建命令（可复现）：**

```bash
git clone --depth 1 https://github.com/chinese-poetry/chinese-poetry.git
python tools/ingest.py --cp-json ./chinese-poetry \
  --werneror <Werneror CSVs> --out data/baichuan.db --to-hant
```

### 附件（v0.2.0）

| 文件 | 大小 | sha256 |
|---|---|---|
| `baichuan.db.gz` | 462 MB（原库 1,118 MB，压缩比 2.42:1） | `806d63f301844820db18d529532f939a93822f9716faf43d1060d813c84911b2` |

解压后 `baichuan.db` 的 sha256 应为
`a1aad50f264514a0fcc58e7eb666ca7ba492f72af8f4f869149a58f03baf88a4`

---

## v0.1.0 —— 首次发布

### 附件

| 文件 | 大小 | sha256 |
|---|---|---|
| `baichuan.db.gz` | 474 MB（原库 1,098 MB，压缩比 2.31:1） | `67b695b904a6be9ecc2b713369922cc077de5ff37c1303a9e502c51bae338e22` |

解压后 `baichuan.db` 的 sha256 应为
`61e1b1d58978c7dcd97dae58b55717d946b6f5c7d3f7f410ce9eac8de9920fbf`
（已验证：`gzip -dc baichuan.db.gz | sha256sum` 与原始库逐字节一致）。

## 校验方法

```bash
# 1. 校验压缩包
echo "67b695b904a6be9ecc2b713369922cc077de5ff37c1303a9e502c51bae338e22  baichuan.db.gz" \
  | sha256sum -c -

# 2. 解压
gzip -dk baichuan.db.gz

# 3. 校验解压结果
echo "61e1b1d58978c7dcd97dae58b55717d946b6f5c7d3f7f410ce9eac8de9920fbf  baichuan.db" \
  | sha256sum -c -

# 4. 自检
sqlite3 baichuan.db "PRAGMA quick_check"          # 应输出 ok
sqlite3 baichuan.db "SELECT count(*) FROM poems_zh_hans"   # 1,023,006
```

## 内容

| | 简体库 `_zh_hans` | 繁体库 `_zh_hant` |
|---|---|---|
| 诗 | 1,023,006 | 1,008,299 |
| 作者 | 29,912 | 29,983 |
| 别名 | 241（33 组） | 同 |

| 朝代 | 数量 | | 朝代 | 数量 |
|---|---|---|---|---|
| 宋 | 404,916 | | 南北朝 | 4,574 |
| 明 | 254,604 | | 魏晋 | 3,035 |
| 清 | 118,156 | | 民国 | 1,948 |
| 唐 | 102,836 | | 隋 | 1,311 |
| 元 | 63,720 | | 先秦 | 910 |
| 近现代 | 31,841 | | 五代 | 600 |
| 当代 | 28,208 | | 两汉 | 356 |
| 金 | 5,748 | | 辽 / 秦 | 22 / 2 |

来源：chinese-poetry 371,313 首（唐宋为主）+ Werneror/Poetry 651,693 首（先秦至当代）。
跨源按 `sha256(段落拼接)` 精确去重，重复 201,692 条。

## 建库方式（不想下 474 MB 就自己跑）

见 README「快速开始」，全程约 3 分钟：

```bash
python tools/fix_data.py --apply      # 校验修补（可回滚）
python tools/ingest.py --old <poetry.db> --werneror <Werneror目录> \
                       --out data/baichuan.db --to-hant
python tools/build_aliases.py
```

## 数据修正记录

进库前已修正上游四类问题（详见 `docs/findings.md`）：

| 问题 | 规模 |
|---|---|
| 朝代系统性错误（全宋诗被灌成唐） | 250,720 首 |
| 繁简匹配失效（陸遊 ≠ 陆游） | 19,707 首 |
| 南唐二主误判（李煜挂唐） | 94 首 |
| OpenCC 短语污染（海内存→海记忆体） | 1 首 |

## 授权

代码 MIT。数据随各源协议（chinese-poetry、Werneror/Poetry 均为 MIT）。
本项目只做归一化、校验、索引与接口，不主张原始文本著作权。
