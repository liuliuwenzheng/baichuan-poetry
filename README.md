# 百川 · Baichuan Poetry

> **从先秦到当代，从中文到世界。**
>
> 一个开源、多源、多语言的诗歌数据工程：把散落各处的诗歌语料汇入同一条江河，
> 并提供可直接使用的离线库与开放接口。

取名自汉乐府《长歌行》：「**百川东到海，何时复西归**」——各朝代、各国语言的诗歌，
像江河一样汇入一个海。

---

## ⚠️ 先读这个：来源与授权

**本项目是「汇集 / 衍生」作品，不是原创数据集。** 诗歌文本全部来自第三方开源项目，
我们只做归一化、校验、去重、索引与查询接口：

| 来源 | 授权 | 版权 |
|---|---|---|
| [chinese-poetry](https://github.com/chinese-poetry/chinese-poetry) | MIT | © 2016 JackeyGao |
| [Werneror/Poetry](https://github.com/Werneror/Poetry) | MIT | © 2018 Werner |
| [palemoky/chinese-poetry-api](https://github.com/palemoky/chinese-poetry-api)（`poetry.db` 数据包来源） | **GPL-3.0** ⚠️ | 见下方说明 |

- **未使用任何第三方代码**；本仓库的 `tools/` 与 `query/` 均为原创实现。
- v0.1.0 的唐宋部分导入自 `poetry.db`（GPL-3.0 项目的 dist 产物）。GPL 对「程序输出」
  的适用性有争议，**为消除歧义，v0.2.0 将直接从 MIT 原始数据（chinese-poetry 的 JSON）
  重建，完全绕开该产物**。
- 完整版权声明、许可全文、引用方式 → **[NOTICE.md](NOTICE.md)**

## 这是什么

网上诗歌数据集不少，但普遍存在三个问题：

1. **断代**：要么只有唐宋（chinese-poetry），要么只有明清（另一批），拼起来才完整；
2. **有错**：朝代字段系统性错误、简繁转换污染、同名异人混作一人；
3. **不可用**：给一堆 JSON/CSV 就完事，没有统一 schema、没有查询工具、没有接口。

百川要把这三件事一起解决：**多源归一化 + 数据校验 + 开放接口**。

## 数据来源与授权

| 来源 | 内容 | 数量 | 协议 |
|---|---|---|---|
| [chinese-poetry](https://github.com/chinese-poetry/chinese-poetry) | 全唐诗、全宋诗、宋词、元曲、诗经、楚辞、论语、蒙学、五代诗词、纳兰性德 | 371,313 首 | MIT |
| [Werneror/Poetry](https://github.com/Werneror/Poetry) | 先秦至当代，含明清、魏晋南北朝、隋、辽金、近现代 | 853,385 首（去重后入库 651,693） | MIT |
| （规划中）世界诗歌 | 英、日、波斯、俄、西、德等语种，原文 + 中文 + 英文 | — | 按源标注 |

本项目只做**归一化、校验、索引与接口**，不主张原始文本的著作权。

## 成果

合并去重后的 **`baichuan.db`（1.1 GB）**：

| | 简体库 | 繁体库 |
|---|---|---|
| 诗 | **1,023,006** 首 | 1,008,299 首 |
| 作者 | 29,912 位 | 29,983 位 |
| 别名 | 241 条（33 组） | 同 |

按朝代（简体）：

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

**补齐前 vs 补齐后**（同一批诗人，之前 chinese-poetry 里根本查不到）：

| 诗人 | 补齐前 | 补齐后 | | 诗人 | 补齐前 | 补齐后 |
|---|---|---|---|---|---|---|
| 陶潜（陶渊明） | ❌ 0 | **151 首** | | 高启 | ❌ 0 | 910 首 |
| 龚自珍 | ❌ 0 | 610 首 | | 唐寅 | ❌ 0 | 404 首 |
| 纳兰性德 | 有（词） | 455 首 | | 庾信 | ❌ 0 | 350 首 |
| 于谦 | ❌ 0 | 443 首 | | 鲍照 | ❌ 0 | 221 首 |
| 谢灵运 | ❌ 0 | 114 首 | | 袁枚 | ❌ 0 | 80 首 |

> 查「陶渊明」要能命中「陶潜」——正式名与习惯名差一层，等于一半的诗对普通人不可见，
> 所以有 `author_aliases` 表（见 `tools/build_aliases.py`）。

## 已修正的上游数据问题

进库前会跑一遍校验与修补（`tools/fix_data.py`），目前修掉的：

| 问题 | 规模 | 说明 |
|---|---|---|
| **朝代系统性错误** | 250,720 首 | 上游把「全宋诗」整批灌进了「唐」：宋只有 21,091 首，实际应为 271,806 首 |
| **简繁转换污染** | 1 首 | OpenCC `s2twp` 短语误匹配：「海**内存**知己」→「海**记忆体**知己」 |
| **繁简匹配失效** | 19,707 首 | 繁体作者名是「陸遊/楊萬裏」，清单里是「陆游/杨万里」，不归一化就整批漏判 |
| **南唐二主误判** | 94 首 | 李煜同时在宋/唐两份清单里，被"歧义"规则吞掉，一直挂着「唐」 |
| 缺重要诗人 | 整个时期 | chinese-poetry 里**没有陶渊明**，也没有清代诗歌（仅 258 首）→ 由 Werneror 源补齐 |

修法原则：**只改有权威清单背书的**。用上游 `全唐诗/authors.song.json`（8,934 位宋诗人）
等原始清单重判作者朝代，再保守传播——只修「诗标为唐」这一种已知默认错误，
避免把无名氏的元曲改成宋、把花间集改成唐、把《诗经》改成"其他"。详见 `docs/findings.md`。

## 目录结构

```
baichuan-poetry/
├── data/                 # 构建产物（baichuan.db、索引），不入 git
├── tools/                # 构建与校验脚本
│   ├── fetch_sources.py  # 拉取各上游数据源
│   ├── fix_data.py       # 数据校验与修补（朝代、简繁污染）
│   ├── ingest.py         # 多源归一化导入
│   └── build_index.py    # 意象/名句倒排索引
├── query/                # 查询 CLI（poem.py 等）
├── docs/
│   ├── schema.md         # 数据库 schema（多语言设计）
│   └── findings.md       # 数据质量发现清单
└── api/                  # 开放接口（REST / GraphQL）
```

## 快速开始

```bash
# 1. 取源（chinese-poetry 的 poetry.db + Werneror 的 34 个 CSV）
#    chinese-poetry: 见 chinese-poetry-api（诗泉）releases；Werneror:
git clone --depth 1 https://github.com/Werneror/Poetry.git

# 2. 校验修补（朝代系统性错误、简繁污染）——默认干跑，--apply 才写
python tools/fix_data.py            # 干跑看影响面
python tools/fix_data.py --apply

# 3. 多源归一化导入（约 75s，出 1.1 GB）
python tools/ingest.py --old <poetry.db> --werneror <Werneror目录> \
                       --out data/baichuan.db --to-hant

# 4. 别名库（陶渊明 ↔ 陶潜）
python tools/build_aliases.py

# 5. 直接查（沿用既有 CLI，无需改代码）
python query/poem.py --db data/baichuan.db random --author 陶潜
python query/poem.py --db data/baichuan.db random --dynasty 明
python query/poem.py --db data/baichuan.db search "菊花" --limit 5
```

## 路线图

- [x] 唐宋库落地（chinese-poetry v0.6.0，37 万首）
- [x] 朝代系统性错误修复（25 万首归位）
- [x] 补齐先秦—当代（Werneror 85.3 万首 → 去重后新增 65.2 万）
- [x] 多源归一化 schema（`source` / `lang_original` / 译文表 / 世界诗表）
- [x] 简繁双库同步（繁体库同 100.8 万首）
- [x] 别名库（正式名 ↔ 习惯名）
- [ ] 开放 REST / GraphQL 接口
- [ ] 意象/名句倒排索引迁到新库
- [ ] 世界诗歌：英、日、波斯等，原文 + 中文 + 英文三语对照

## 已知限制

- **异文未合并**：跨源去重按 `sha256(段落拼接)` 精确匹配，同一首诗若字词或标点有别
  会两条并存（实测标点差异约 0.6%，多为「培𪣻/培塿」这类异体字）。宁存两条，不改文本。
- **体裁只判能确定的**：Werneror 源无体裁信息，只推断四句/八句的绝句律诗，
  其余留空（约 25 万首）。猜错比留空更糟。
- **上游"重出诗"保留**：同一首诗挂两个作者名是上游有意为之，未去重。
- **繁简为机械转换**：Werneror 源只有简体，繁体版由 `zhconv` 的 `zh-hant` 直转生成
  （不用 `s2twp`，避免「海内存」→「海记忆体」那类短语误匹配）。

## 授权

本项目代码 MIT；数据随源协议（均为 MIT）。

## 致谢

本项目完全建立在他人成果之上，在此明确致谢：

- [chinese-poetry](https://github.com/chinese-poetry/chinese-poetry) — **MIT, © 2016 JackeyGao**
  （及其 [palemoky fork](https://github.com/palemoky/chinese-poetry) fix-typo 分支）
- [Werneror/Poetry](https://github.com/Werneror/Poetry) — **MIT, © 2018 Werner**
- [palemoky/chinese-poetry-api](https://github.com/palemoky/chinese-poetry-api)（诗泉）— GPL-3.0
  项目的 `poetry.db` 数据包，是 v0.1.0 唐宋部分的来源

**版权声明、许可全文与正确引用方式见 [NOTICE.md](NOTICE.md)。**
