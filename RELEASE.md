# Release 说明 · 数据包

数据库不入 git（1.1 GB，超过 GitHub 单文件 100 MB 限制），以 **Release 附件** 发布。

## 附件

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
