# 从「译成 PDF」到「读成 / 译成 Markdown」：我们魔改 PDFMathTranslate 的结构化导出流水线

> 基于开源项目 [PDFMathTranslate / pdf2zh](https://github.com/Byaidu/PDFMathTranslate)（本地目录 `EssayPod/PDFMathTranslate-main`）的二次改造说明。  
> 目标：在保留原有 PDF 叠字翻译能力的前提下，新增一条学术论文 → **结构化 Markdown**（原文 + 可选中文译文）导出链路。

---

## 一、为什么要改？

原版 PDFMathTranslate 擅长一件事：把论文翻成**版式几乎不变的双语 PDF**。它内部其实已经用 DocLayout-YOLO 认出了标题、正文、图、表，但随后会把类别压成「能不能译」的像素 mask，最终只吐出 `*-mono.pdf` / `*-dual.pdf`。

对我们来说，更需要的是另一条路：

- 先把论文收成**可读、可检索、可喂给下游（摘要 / Podcast / RAG）的 Markdown**
- 按阅读顺序组织标题、段落、图片、表格
- 在**不破坏 Markdown 结构**的前提下，得到中文译本（`*.zh.md`）

于是我们在上游项目上做了一条旁路：**Markdown Export + Block-level Translate Pipeline**。原有 PDF 叠字翻译路径保持不动。

---

## 二、现在多了什么能力？

| 能力 | 说明 |
|------|------|
| 原语言 Markdown 导出 | 结构化写出 `<stem>.md` + `assets/` |
| 中文 Markdown 翻译 | 默认同时写出 `<stem>.zh.md`（可用 `--no-translate` 关闭） |
| 版面语义保留 | 使用 YOLO 的 `title` / `plain text` / `figure` / `table` / caption / formula 等类型 |
| 阅读顺序 | 单栏：上→下；双栏：左栏整列 → 右栏整列（全宽标题/通栏图作分隔） |
| 跨页拼段 | 仅合并跨页被切开的正文 `plain text`，图注/表注不并入正文 |
| 标题层级 | 按字号将 `title` 映射为 `#` / `##` / `###` |
| 图 / 表 / 公式落点 | 按 bbox 裁图到 `assets/`，在 MD 中插入 `![](...)`，题注紧随其后 |
| 文本去噪 | 抑制裁剪残片（如 `pp`/`g`）、数学字体泄漏、句末脚注角标粘连 |
| 列表项保形翻译 | 同一段内的 `– RQ1/RQ2/...` 等列表项拆开翻译，避免粘成一行 |

当前**刻意不做**的部分（留给后续迭代）：

- 表格还原成 `|...|` 单元格结构
- 公式转 LaTeX
- 复杂多栏 / 杂志式异形版面
- 术语表 / 专用 LLM 学术译法（当前复用 pdf2zh 的翻译后端，默认 Google）

---

## 三、流水线怎么走？

```text
PDF
  ├─ 逐页渲染 → DocLayout-YOLO（保留 type + bbox + conf）
  ├─ PyMuPDF 按框抽文本 / 裁切图、表、公式
  ├─ 页内阅读顺序排序（含双栏检测）
  ├─ 跨页正文合并 + 章节号与标题拼接
  ├─ 标题字号分档
  ├─ 序列化为 <stem>.md + assets/*.png
  └─ （默认）Block 级并行翻译 → <stem>.zh.md（共享 assets/）
```

和原版翻译链路的关系：

```text
pdf2zh CLI
  ├─ （默认）KernelRegistry → high_level.translate → mono/dual PDF
  └─ （新增）--markdown → markdown_export.export_*
           ├─ 结构导出 → <stem>.md
           └─ Block 翻译（复用 translator.py）→ <stem>.zh.md
```

Markdown 路径**复用**：

- `pdf2zh.doclayout.OnnxModel`（布局）
- `pdf2zh.translator.*`（翻译后端与缓存）

**不进入** `TranslateConverter` 的 PDF 叠字回写。

---

## 四、翻译功能说明

### 4.1 设计原则：结构壳本地生成，只译纯文本

翻译发生在 **Block IR 上**，而不是对整份 Markdown 文件整篇投喂。这样 `#` / `![]()` / 题注样式等语法壳不会被翻译引擎改坏。

流程：

1. 先完成结构化 Block 列表（与写英文 MD 同一套）  
2. 对可译 Block 的 `.text` 做并行翻译  
3. 用同一套 `blocks_to_markdown()` 渲染出中文 MD  

### 4.2 翻译颗粒度（推荐且已实现）

**主粒度 = 一个可译 Block 的文本字段**（与原版 pdf2zh「按段翻译」一致，但语义类型来自 YOLO，而非 mask ID）。

| Block 类型 | 是否翻译 | 回填方式 |
|------------|----------|----------|
| `title` | 译标题正文 | 本地拼回 `#` / `##` / `###` |
| `plain text` | 译正文 | 仍输出为段落；列表项见下 |
| `figure_caption` / `table_caption` / `table_footnote` / `formula_caption` | 译题注文字 | 本地拼回 `*...*` |
| `figure` / `table` / `isolate_formula` | **不译** | `![](assets/...)` 原样保留 |

**细化规则（plain text 内）：**

- PDF 软换行：项内多行合并成一句再译（去断词连字符）  
- **列表项**（以 `–` / `-` / `•` / `1.` / `1)` 等开头）：拆成**多个翻译 unit**，各自调用 `translate()`，再用换行拼回  
  - 避免出现：`- RQ1 ...? - RQ2 ...? - RQ3 ...?` 粘在同一行  
- 不按「整文件 MD」或「逐词」翻译  

对应代码：`TRANSLATABLE_TYPES`、`split_plain_text_units()`、`translate_blocks()`。

### 4.3 线程池并行

与原版 `converter.py` 相同思路：

- 使用 `concurrent.futures.ThreadPoolExecutor`  
- 并行单位是 **翻译 unit**（通常一个 Block；列表项则是 Block 内的每一条）  
- 线程数由 CLI `-t` / `--thread` 控制（默认 4）  
- 复用 `BaseTranslator.translate()` 的**结果缓存**（`TranslationCache`）与失败重试逻辑  

注意：并行的是「段与段 / 列表项与列表项」的网络请求，不是把一句话拆开并行。

### 4.4 与原版 PDF 翻译的差异（对照）

| 维度 | 原版 PDF 路径 | 本 Markdown 路径 |
|------|----------------|------------------|
| 输入单元 | layout mask 拼出的段落字符串（可含 `{v*}` 公式占位） | YOLO Block 的 `.text` |
| 输出 | 叠字 mono/dual PDF | `<stem>.md` + `<stem>.zh.md` + 共享 `assets/` |
| 公式 | 占位符进译文再贴回字形 | 公式区裁成图片，不译 |
| 跨页 | 基本按页内段落译 | 先跨页合并正文，再译 |
| 结构保证 | 靠几何框回排 | 靠「只译 text、本地渲染 MD」 |

---

## 五、入口在哪里？

### 命令行（主入口）

```bash
# 默认：原文 MD + 中文 MD（Google，4 线程）
.venv/bin/pdf2zh /path/to/paper.pdf \
  --markdown --backend cpu \
  -s google -li en -lo zh -t 4 \
  -o /path/to/out_dir

# 只要结构化原文、不翻译
.venv/bin/pdf2zh /path/to/paper.pdf \
  --markdown --no-translate --backend cpu \
  -o /path/to/out_dir
```

常用参数：

| 参数 | 作用 |
|------|------|
| `--markdown` | 启用结构化 Markdown 导出 |
| `--no-translate` | 只写原文 `<stem>.md`，跳过翻译 |
| `-s` / `--service` | 翻译后端（默认 `google`；也可 `openai`、`deepl` 等） |
| `-li` / `-lo` | 源语言 / 目标语言（默认 `en` → `zh`） |
| `-t` / `--thread` | 翻译线程池大小（默认 4） |
| `-o` / `--output` | 输出目录 |
| `--backend cpu` | ONNX 后端；Mac 上建议 cpu |
| `-p 1-3` | 只处理部分页（便于调试） |
| `--ignore-cache` | 忽略翻译缓存，强制重译 |

示例输出：

```text
out_dir/
  paper.md              # 原语言结构化 Markdown
  paper.zh.md           # 中文译本（lang_out=zh 时后缀为 .zh）
  assets/
    p02_figure_01.png
    p05_table_01.png
    p04_isolate_formula_01.png
```

中英文共享同一套 `assets/`，译文里的图片路径不变。

### 代码入口

| 层级 | 位置 |
|------|------|
| CLI 开关与分流 | `pdf2zh/pdf2zh.py` → `main()`，`--markdown` 时调用导出 |
| 导出 + 翻译实现 | `pdf2zh/markdown_export.py` |
| 对外函数 | `export_files_to_markdown()` / `export_pdf_to_markdown()` / `translate_blocks()` / `build_translator()` |
| 布局模型 | `pdf2zh/doclayout.py`（未改接口，直接复用） |
| 翻译后端 | `pdf2zh/translator.py`（复用，未改协议） |

Python 内调用示例：

```python
from pdf2zh.doclayout import OnnxModel, set_backend
from pdf2zh.markdown_export import export_pdf_to_markdown

set_backend("cpu")
model = OnnxModel.load_available()
paths = export_pdf_to_markdown(
    "paper.pdf",
    "md_out",
    model,
    translate=True,
    service="google",
    lang_in="en",
    lang_out="zh",
    thread=4,
)
# paths → [md_out/paper.md, md_out/paper.zh.md]
```

---

## 六、改了哪些脚本？新增了哪些？

### 新增

| 文件 | 职责 |
|------|------|
| [`pdf2zh/markdown_export.py`](pdf2zh/markdown_export.py) | 核心：检测 → 抽取 → 排序 → 合并 → 分档 → 写 MD →（可选）Block 级并行翻译 |
| [`MARKDOWN_EXPORT_PIPELINE.md`](MARKDOWN_EXPORT_PIPELINE.md) | 本文档 |

`markdown_export.py` 内主要符号：

| 符号 | 功能 |
|------|------|
| `detect_page_blocks` | 一页内 YOLO + 文本/资源抽取 |
| `sort_blocks_reading_order` / `_is_two_column_page` | 双栏阅读顺序 |
| `merge_cross_page_paragraphs` / `merge_section_number_titles` | 跨页与章节标题修复 |
| `assign_heading_levels` | H1–H3 |
| `_extract_text_and_fontsize` / `_clean_extracted_text` | 安全抽文本与去噪 |
| `build_document_blocks` | 组装全文 Block IR |
| `blocks_to_markdown` | Block → Markdown 字符串（结构壳本地生成） |
| `build_translator` | 按 `-s` 构造 pdf2zh 翻译器 |
| `split_plain_text_units` | 正文软换行合并 + **列表项拆分** |
| `translate_blocks` | 线程池并行翻译可译 unit，回填 `.text` |
| `export_pdf_to_markdown` / `export_files_to_markdown` | 落盘原文 / 译文 MD |

### 修改

| 文件 | 改动要点 |
|------|----------|
| [`pdf2zh/pdf2zh.py`](pdf2zh/pdf2zh.py) | 增加 `--markdown`、`--no-translate`；在 `main()` 中于 PDF 翻译内核之前分流到 `export_files_to_markdown`，并传入 `-s/-li/-lo/-t` 等翻译参数 |

### 未改动（刻意保留）

原 PDF 叠字翻译主路径相关文件基本未动，例如：

- `pdf2zh/high_level.py`（PDF 叠字翻译）
- `pdf2zh/converter.py`（段落翻译与 PDF 算子回写）
- `pdf2zh/translator.py`（各翻译后端；Markdown 路径只复用）
- `pdf2zh/doclayout.py`（仅复用）

因此：**`pdf2zh paper.pdf` 老用法仍然可用**；Markdown 是附加能力，不是替换。

---

## 七、关键实现细节（魔改要点）

### 1. 不要丢掉 YOLO 的 type

原版在 `high_level.translate_patch` 里把检测框打成 mask，类别名随之消失。Markdown 路径改为保留：

`title | plain text | figure | figure_caption | table | table_caption | isolate_formula | ...`

### 2. 双栏 = 左整列再右整列

检测到左右窄栏垂直重叠后：

1. 全宽块（通栏标题、宽图）按 y 切开「条带」  
2. 每个条带内：左栏自上而下，再右栏自上而下  

已在双栏论文（如 MCP-Guard）上验证：`## 1 Introduction` → `## 2 Related Work` → `### 2.1` 顺序恢复正常。

### 2.1 并排子图 (a)/(b)

YOLO 常把左右子图拆成两个半宽 `figure`，再检出 `(a)`、`(b)`、拼接行、`Figure N` 多条题注。若只按流式排序，会出现：正文被图切断、题注重复、图与题注错位。

对应修复（`organize_side_by_side_floats`）：

1. 水平相邻且 y 重叠的 figure/table 打成同一个 `float_group`，按通栏锚点参与阅读顺序  
2. 题注按几何归属后去重：保留 `(a)`/`(b)` + `Figure N`，丢掉拼接重复行  
3. 组内固定顺序：左图 → 右图 → 子题注 → 总题注  
4. 被图切断的正文（含跨页）再拼回完整段落，图注块整体跟在段落后  
5. 题注归属收紧：禁止把「下一张表」的标题并进上一组（曾导致右栏 `tic workflows.` 被提前）；双栏断词 `agen-` + `tic workflows.` 拼回 `agentic workflows.`  

### 2.2 独立公式编号统一在下方

PDF 里方程号 `(N)` 常与公式同一基线、靠右。按 y/x 流式排序时，下一式的编号可能排到上一式和本式公式之间（甚至跑到公式上方）。

`pair_formula_equation_numbers`：按垂直距离把 `isolate_formula` 与 `(N)` 配对，打成 `float_group`，固定顺序为 **公式图 → 编号**，Markdown 里编号始终在公式下面。

### 3. 文本残片从哪来、怎么压

曾出现段间莫名 `pp` / `g` / `f` / `y`，根因主要是：

- 裁剪框纵向 padding 切碎邻行字形 → MuPDF 吐出碎片  
- 脚注角标 / 绘图碎字符粘进正文框  
- （早期过度过滤）把 CMMI 等数学字体整段丢掉 → 行内式变成 `where ˆ =.`

对应修复：

- 取消纵向 pad；只丢弃**非数学**的过小字号与 `_`/`|` 碎屑  
- **保留**行内数学字体（CMMI/CMSY/MSAM 等）  
- 用 span 基线区分上/下标，拼 Unicode（`ŷᵢ`、`Tᵤ`、`ᵢ₌₁`）；合并分拆重音 `ˆ`+`y`  
- 行内若有数学，保留旁路小号 CMR（如 `=1`）；去掉 CMEX 私用区括号碎片  
- 清理短碎片行；句末角标清理避开 `e.g.` / `i.e.`  

独立公式区仍裁成图片；行内符号走 Unicode，**不**还原 LaTeX。跨行展示公式（极限、cases）仍可能拆成多行纯文本。

### 4. 翻译时如何保住 Markdown 格式

- **不**把整份 `.md` 丢给翻译引擎  
- 只译 `Block.text`；`heading_level`、`asset_path`、type 不变  
- 渲染阶段再次套用 `#` / 图片语法 / 题注斜体  
- 列表项拆 unit：防止「多条 bullet 被压成一段」  

### 5. 已知译质问题（引擎侧，非结构问题）

免费 Google 可能出现术语误译（如把 LLM 译成「法学硕士」、Abstract 译成「抽象的」）。结构仍正确。需要更高译质时可换 `-s openai` 等后端。

---

## 八、怎么快速验收？

```bash
# 单栏：原文 + 中文
.venv/bin/pdf2zh \
  EssayPod/tasks/4e5dd31e04ab4f688731ecc3ab761522/input.pdf \
  --markdown --backend cpu \
  -s google -li en -lo zh -t 4 \
  -o EssayPod/tasks/4e5dd31e04ab4f688731ecc3ab761522/md_out

# 双栏：可先只要结构
.venv/bin/pdf2zh \
  EssayPod/tasks/67bd536ccf23407c9ae23f418685b2b4/input.pdf \
  --markdown --no-translate --backend cpu \
  -o EssayPod/tasks/67bd536ccf23407c9ae23f418685b2b4/md_out
```

检查清单：

1. 标题层级是否合理（中英文标题数应一致）  
2. 双栏章节顺序是否左→右、不锯齿交错  
3. 图/表路径是否仍指向同一 `assets/`，题注是否已译  
4. 段间是否还有明显字母碎片  
5. 列表（如 RQ1/RQ2/RQ3）是否**各占一行**，未粘成一段  
6. `input.md` 与 `input.zh.md` 的 `#` / `![` 数量是否对齐  

---

## 九、和 EssayPod 的关系（展望）

这条流水线目前挂在 `PDFMathTranslate-main` 内，可独立使用。自然接入点是 EssayPod 任务目录：

上传 PDF → 导出 `md_out/<stem>.md` + `md_out/<stem>.zh.md` → 再进入摘要 / 播客脚本。

相对「先译成 PDF 再抽文本」，直接消费结构化中文 Markdown 更适合下游 LLM 任务。

---

## 十、一句话总结

我们把 PDFMathTranslate 从「只会产出译后 PDF 的翻译器」，扩展成了「先看懂版面 → 按阅读顺序写成 Markdown → 再按 Block（及列表项）并行翻译成中文 MD」的文档解析器——入口是 `--markdown`，心脏是新建的 `pdf2zh/markdown_export.py`，翻译复用原有 `translator` 与线程池，对主 PDF 翻译链路保持兼容、最小侵入。
