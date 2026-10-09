# 来源与致谢 · Credits, Attribution & Licensing

> **百川（Baichuan Poetry）是「汇集 / 衍生」作品，不是原创数据集。**
>
> 本仓库的全部诗歌**文本**均来自下列第三方开源项目。我们只做归一化、校验、去重、
> 索引与查询接口。原始文本的著作权与许可归各来源所有，**我们不对原始文本主张任何权利**。

---

## 一、数据来源

### 1. chinese-poetry —— 唐宋诗词曲等

- 项目：<https://github.com/chinese-poetry/chinese-poetry>
- 授权：**MIT License**
- 版权：**Copyright (c) 2016 JackeyGao**
- 贡献：全唐诗、全宋诗、宋词、元曲、诗经、楚辞、论语、蒙学、五代诗词、纳兰性德等
- 说明：诗歌文本本身是古代作品，属公有领域；该项目的整理、标点、结构化成果以 MIT 授权。

### 2. Werneror/Poetry —— 先秦至当代

- 项目：<https://github.com/Werneror/Poetry>
- 授权：**MIT License**
- 版权：**Copyright (c) 2018 Werner**
- 贡献：先秦、秦、汉、魏晋、南北朝、隋、唐、宋、辽、金、元、明、清、民国、近现代、
  当代各期诗歌，共 853,385 条

### 3. palemoky/chinese-poetry-api（诗泉）—— ✅ v0.2.0 起已完全移除

- 项目：<https://github.com/palemoky/chinese-poetry-api>
- 授权：**GNU GPL-3.0**（约束的是该项目的**程序代码**）
- 与本项目的关系：**历史关系，现已切断。**

**为什么曾经相关，以及怎么解决的**

1. **v0.1.0** 的唐宋部分，是通过该项目 Release 的数据库产物 `poetry.db` v0.6.0 导入的。
2. 本仓库**从未使用该项目任何一行代码**——`query/poem.py` 是我们自己的 Python 实现，
   与上游的 Go 实现无代码关联。但 GPL 对「程序输出」是否适用，社区存在争议
   （FSF 立场：程序输出通常不受 GPL 约束，除非输出内嵌了程序本身）。
3. **有争议就不赌。** 自 **v0.2.0** 起，唐宋部分改为直接从 **MIT 授权的原始 JSON**
   （chinese-poetry 源码目录）构建，`poetry.db` 已彻底退出构建链路。

> ✅ **v0.2.0 及以后的数据包，不含任何 GPL 项目的产物。**
> 全部由 chinese-poetry（MIT）与 Werneror/Poetry（MIT）两个来源构建而成。

附带的收获：v0.1.0 的繁体表是把简体机械转换来的（上游用了 OpenCC 的 `s2twp`
短语表，把「海内存知己」转成了「海記憶體」）。v0.2.0 改为对**简繁两向**都做
`zhconv` 直转（不用短语表），既保住了原文，也消掉了那类污染。

---

## 二、本仓库的原创部分

下列内容为 **liuliuwenzheng** 原创，以 **MIT License** 授权（Copyright (c) 2026
Baichuan Poetry contributors）：

| 文件 | 说明 |
|---|---|
| `tools/ingest.py` | 多源归一化导入器 |
| `tools/fix_data.py` | 数据校验与修补（朝代归位、简繁污染） |
| `tools/build_aliases.py` | 别名库构建 |
| `query/poem.py` | 查询 CLI（独立 Python 实现） |
| `docs/*`、`README.md`、`RELEASE.md` | 文档 |
| `data/baichuan.db` 的 **schema 设计**与 **加工结果** | 见下 |

**我们的原创贡献是什么**：schema 设计、跨源去重、朝代归位的判定与修正、
简繁归一化匹配、别名库、体裁判定、以及全部导入/校验工具。
**不属于我们原创的**：诗歌文本本身——它属于上述来源。

---

## 三、许可全文

### 附录 A —— chinese-poetry（MIT License）

```
The MIT License (MIT)

Copyright (c) 2016 JackeyGao

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 附录 B —— Werneror/Poetry（MIT License）

```
MIT License

Copyright (c) 2018 Werner

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### 附录 C —— palemoky/chinese-poetry-api（GNU GPL-3.0）

本项目**未使用其代码**，故未包含 GPL-3.0 全文；其数据包产物与本项目的关系
已在上文「一、3」中说明。许可全文见
<https://www.gnu.org/licenses/gpl-3.0.txt>，
项目地址 <https://github.com/palemoky/chinese-poetry-api>。

---

## 四、如何正确引用本项目

如果你用了本仓库的数据，请**同时**致谢上游来源：

> 数据来自 百川 Baichuan Poetry（<https://github.com/liuliuwenzheng/baichuan-poetry>），
> 其诗歌文本来自 chinese-poetry（MIT, © JackeyGao）与 Werneror/Poetry（MIT, © Werner）。

---

## 五、如果您是权利人

若您是上述任一来源的权利人，认为本项目的使用方式不当，请提 Issue，
我们会立即移除相关内容或调整授权方式。
