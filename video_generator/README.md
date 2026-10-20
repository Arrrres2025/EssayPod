# EssayPod Video Generator

将 EssayPod 任务目录中的 **PDF 论文页面**、**播客音频** 与 **字幕** 合成为 **1080p** 讲解视频。默认使用 TTS 阶段写入 `run.log` 的 round 时间戳生成字幕，无需 Whisper；也支持本地 Whisper / stable-ts 对齐作为备选。

**16:9 横屏**支持 Web UI **可拖拽四区域布局**（标题 / 板书 / PDF 页 / 图表），内容在各自区域内自适应；底部字幕保持居中不变。

---

## 目录

- [功能概览](#功能概览)
- [输入与输出](#输入与输出)
- [快速开始](#快速开始)
- [CLI 参数](#cli-参数)
- [处理流水线](#处理流水线)
- [视频规格与预设](#视频规格与预设)
- [画面布局编辑器（16:9）](#画面布局编辑器169)
- [PDF 页面渲染](#pdf-页面渲染)
- [页面时间轴](#页面时间轴)
- [字幕系统](#字幕系统)
- [左侧记忆锚点（板书 Agent）](#左侧记忆锚点板书-agent)
- [主标题与气泡音效（16:9）](#主标题与气泡音效169)
- [右侧 PDF 图表（16:9）](#右侧-pdf-图表169)
- [视频合成](#视频合成)
- [Web UI 集成](#web-ui-集成)
- [模块结构](#模块结构)
- [中间产物](#中间产物)
- [依赖与环境](#依赖与环境)
- [常见问题](#常见问题)
- [后续扩展](#后续扩展)

---

## 功能概览

| 能力 | 说明 |
|------|------|
| PDF 渲染 | PyMuPDF 将 PDF 每页渲染为 PNG，自动在 **References / 参考文献** 页截断 |
| 固定 1080p 画布 | 横屏 1920×1080、竖屏 1080×1920；16:9 下 PDF 渲染到 **page 区域**，非整页铺满 |
| 可拖拽布局 | **仅 16:9**：Web UI 调整标题 / 板书 / PDF / 图表四区域位置与大小，保存为 `layout.json` |
| 播客时间戳字幕 | 从 `run.log` 解析 `[round start]` / `[round end]`，与 `transcript.txt` 合并 |
| 子句级字幕 | 按 `，`、`。` 切分，加权分配子时间戳，每条字幕只显示一个子句 |
| 智能换行 | 避免孤行、保护 `是不是`、整段保留 `[CONTINUES]` 等 bracket token |
| 圆角半透明底 | ASS 矢量绘制圆角黑底（30% 透明），白字 PingFang SC |
| 首条字幕淡入 | 仅第一条字幕 cue 带 280ms 淡入淡出，其余默认无动画 |
| 首页放大动画 | 第 1 页 1s zoompan（0.90→1.0）+ fade in，其余页静态 |
| 首页 2× 时长 | 页面时间按权重 `[2, 1, 1, …]` 分配，首页展示时间约为其他页 2 倍 |
| Whisper 备选 | `--subtitle-source whisper`，支持 stable-ts 强制对齐或 whisper 转写 |
| 增量复用 | `--skip-align` 可复用已有 `subtitles.ass` 或从 `subtitles.json` 重建 ASS |
| 左侧记忆锚点 | **仅 16:9**：结构化提取 4～6 条锚点（问题→创新→结果），标题+补充说明 |
| 主标题 | **仅 16:9**：从 `condensed.txt` 的 `【标题】` 段解析，第 5 秒出现并保留至结束 |
| 气泡音效 | **仅 16:9**：每条板书标题出现时混入 `data/bubble_sound.mp3` |
| 右侧 PDF 图表 | **仅 16:9**：提取 Figure 插图，离开该页时在右侧空白区滑入并保留 |
| Web UI | EssayPod 网页端播客完成后可选 16:9 / 9:16 制作；16:9 支持布局编辑、预览与下载 |

---

## 输入与输出

### 必需输入（`<task-dir>/`）

| 文件 | 用途 |
|------|------|
| `input.pdf` | 论文 PDF |
| `podcast.mp3` | 播客音频，决定视频总时长 |
| `transcript.txt` | 逐 round 文本，格式：`[round N] Speaker: 文本` |

### 可选输入

| 文件 | 用途 |
|------|------|
| `run.log` | TTS 阶段日志，含 round 起止时间戳（默认字幕来源） |
| `transcript_timed.json` | 若已存在则优先作为时间戳来源，跳过解析 `run.log` |
| `condensed.txt` | 16:9 主标题来源（解析 `【标题】` 段） |

### 默认输出（`<task-dir>/video/` 或 `--work-dir` 指定目录）

| 路径 | 说明 |
|------|------|
| `paper_video.mp4` | 最终视频（可用 `--output` 覆盖） |
| `pages/page_0001.png` … | PDF 各页 PNG |
| `subtitles.srt` | Round 级 SRT 字幕 |
| `subtitles.json` | Round 级字幕 JSON |
| `subtitles_clauses.json` | 子句级字幕 JSON（ASS 生成前展开） |
| `subtitles.ass` | 烧录用 ASS（含圆角底 + 文本双层 Dialogue） |
| `page_timeline.json` | 每页起止时间与对应图片路径 |
| `pages.concat.txt` | ffmpeg concat demuxer 页面列表（调试用） |
| `clips/page_0001.mp4` … | 各页独立视频片段 |
| `clips.concat.txt` | 片段拼接列表 |
| `key_points.json` | 板书 Agent 输出的记忆锚点（仅 16:9） |
| `key_points.ass` | 标题 + 板书 ASS 层（单独烧录，位于 PDF 之上） |
| `layout.json` | 16:9 四区域布局配置（Web UI 保存或 pipeline 默认生成） |
| `video.ass` | 字幕 + 锚点合并 ASS（调试用；成片分两次烧录） |
| `podcast_with_bubbles.mp3` | 混入板书气泡音效后的音频（仅 16:9，有锚点时） |
| `page_images/` | 从 PDF 提取的 Figure 图片 PNG/JPEG（仅 16:9） |
| `page_images.json` | 图表出现时间与路径元数据 |
| `page_image_clips/` | 右侧图表滑入动画 overlay 片段 |
| `video_run.log` | Web UI 或后台触发的视频生成日志 |

同一任务可分别生成横屏与竖屏，建议使用不同 `--work-dir`：

```bash
python -m video_generator.run --task-dir tasks/<id> --format 16:9 \
  --work-dir tasks/<id>/video_16x9 --output tasks/<id>/video_16x9/paper_video.mp4

python -m video_generator.run --task-dir tasks/<id> --format 9:16 \
  --work-dir tasks/<id>/video_9x16 --output tasks/<id>/video_9x16/paper_video.mp4
```

---

## 快速开始

```bash
cd EssayPod
pip install -r video_generator/requirements.txt   # 至少需要 pymupdf
brew install ffmpeg                                # macOS；需系统 ffprobe

# 默认：播客 round 时间戳 + 16:9 横屏
python -m video_generator.run --task-dir tasks/11bc60b8846049568963b65e6dcc1ad1
```

---

## CLI 参数

```
python -m video_generator.run --task-dir <path> [options]
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--task-dir` | （必填） | EssayPod 任务目录 |
| `--output` | `<work-dir>/paper_video.mp4` | 输出 MP4 路径 |
| `--work-dir` | `<task-dir>/video` | 中间产物目录 |
| `--format` | `16:9` | `16:9` 或 `9:16` |
| `--pdf-dpi` | `200` | PDF 渲染 DPI，越高缩放后越清晰 |
| `--max-pages` | 无限制 | 仅渲染前 N 页（快速测试） |
| `--subtitle-source` | `podcast` | `podcast` 或 `whisper` |
| `--whisper-model` | `small` | Whisper 模型名 |
| `--align-mode` | `auto` | `auto` / `forced` / `transcribe` |
| `--subtitle-fade-ms` | `0` | 全局字幕淡入淡出毫秒数（0=关闭） |
| `--skip-align` | false | 复用已有字幕，跳过重新对齐 |
| `--skip-key-points` | false | 跳过板书 Agent；若存在 `key_points.json` 则复用 |
| `--skip-page-images` | false | 跳过 PDF 图表提取与右侧 overlay（仅 16:9） |

入口文件：`run.py`（亦可通过 `python -m video_generator` 调用 `__main__.py`）。

---

## 处理流水线

`pipeline.generate_video()` 按以下顺序执行：

```
input.pdf ──► pdf_pages.render_pdf_pages() ──► pages/*.png
                                                      │
podcast.mp3 ──► probe_duration_seconds() ────────────┤
                                                      │
transcript.txt + run.log ──► podcast_timestamps ──► subtitles.json (round 级)
                              或 whisper align                              │
                                                      ▼
                              clause_timestamps.expand_segments_by_clauses()
                                                      │
                                                      ▼
                              align.write_ass() ──► subtitles.ass (底部字幕)
                                                      │
                              [16:9 only] resolve_layout() ──► layout.json
                                                      │
                              [16:9 only] key_points_agent ──► key_points.json
                                                      │
                              condensed.txt ──► key_points.ass (标题 + 板书)
                                                      │
                              merge ──► video.ass (调试合并副本)
                                                      │
                              [16:9 only] mix_key_point_bubbles ──► podcast_with_bubbles.mp3
                                                      │
                              [16:9 only] pdf_images + page_images_render
                                                      │
                              page_image overlay clips (figures 区域滑入)
                                                      │
page_paths + audio_duration ──► timeline.build_equal_page_timeline()
                                                      │
                                                      ▼
                              compose.compose_video() ──► paper_video.mp4
                              (白底 + page 区域 + figures overlay
                               + key_points.ass + subtitles.ass)
```

---

## 视频规格与预设

定义于 `video_presets.py`，通过 `get_video_preset(format_key)` 加载。

| 预设 | 分辨率 | 字幕字号 | 左右边距 | 底边距 | 首条淡入 |
|------|--------|----------|----------|--------|----------|
| `16:9` | 1920×1080 | 54px | 60px | 60px | 280ms |
| `9:16` | 1080×1920 | 60px | 36px | 84px | 280ms |

16:9 下板书/标题字号随画布高度按 720p 基准等比缩放（约 1.5×）。视频编码默认 **libx264 CRF 18**。

公共属性：

- `background_color = "white"`：PDF 未覆盖区域填充白色
- `subtitle_fade_ms = 0`：除首条外，默认无淡入淡出；可通过 `--subtitle-fade-ms` 全局开启
- `first_subtitle_fade_ms = 280`：仅第一条子句 cue 使用

---

## 画面布局编辑器（16:9）

**模块：** `video_layout.py` + Web UI（`static/index.html`）+ `app.py` 布局 API

### 功能

在生成视频前，于 **1920×1080** 画布上调整四个可拖动区域：

| 区域 | 内容 | 框内自适应 |
|------|------|------------|
| `title` | 主标题（`condensed.txt`） | 换行，最多 3 行 |
| `key_points` | 板书记忆锚点 | 换行、压缩行距/间距 |
| `page` | PDF 论文页 | 等比 scale-to-fit |
| `figures` | 右侧 Figure 图表 | 多图纵向堆叠、等比缩小 |

- **拖动标题栏**移动区域，**拖动右下角**缩放区域
- 松开鼠标自动保存；「恢复默认布局」还原为经典左板书 + 中 PDF + 右图
- **竖屏 9:16 不支持**自定义布局，仍为整页居中缩放

### 默认布局

`compute_default_layout()` 将旧版隐式排版转为显式区域（与改版前视觉一致）：

```
┌ title ─────────────────┐
│ key_points             │  ┌── page ──┐  ┌ figures ┐
│                        │  │          │  │         │
└────────────────────────┘  └──────────┘  └─────────┘
                    （底部字幕居中，不属于布局区域）
```

### `layout.json` 格式

```json
{
  "canvas_width": 1920,
  "canvas_height": 1080,
  "regions": {
    "title":      { "x": 45,  "y": 72,  "w": 596, "h": 126 },
    "key_points": { "x": 45,  "y": 198, "w": 596, "h": 798 },
    "page":       { "x": 542, "y": 0,   "w": 834, "h": 1080 },
    "figures":    { "x": 1377,"y": 198, "w": 542, "h": 798 }
  }
}
```

- 坐标单位为像素，相对画布左上角
- 宽高强制为偶数（H.264 要求）
- 单区域最小约 **80×80** px

### 合成图层顺序（从底到顶）

```
1. 白底画布 + PDF（page 区域，首页含 zoompan intro）
2. Figure 图表 overlay（figures 区域，滑入动画）
3. 标题 + 板书（key_points.ass，ASS Layer 2）
4. 底部字幕（subtitles.ass，最上层）
```

`compose.py` 对 ASS **分两次烧录**：先 `key_points.ass`，再 `subtitles.ass`，确保标题/板书始终在 PDF 像素层之上。

### Web UI 操作

1. 播客完成后选择 **横屏 16:9**
2. 在 **「画面布局」** 区块拖动/缩放四个区域
3. 点击 **「开始制作视频」**（有未保存改动会先保存布局）

### API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/tasks/{id}/video/layout?video_format=16:9` | 读取布局 + 预览数据（标题、板书样例、PDF/图缩略图 URL） |
| `PUT` | `/api/tasks/{id}/video/layout` | JSON body：`{ "video_format": "16:9", "layout": { ... } }` |
| `POST` | `/api/tasks/{id}/video/layout/reset` | 表单 `video_format=16:9`，恢复默认布局 |
| `GET` | `/api/tasks/{id}/video/layout/preview-page` | 布局编辑器 PDF 预览图 |
| `GET` | `/api/tasks/{id}/video/layout/preview-figure?name=...` | 布局编辑器 Figure 预览图 |

Pipeline 启动时调用 `resolve_layout()`：若已有 `layout.json` 且分辨率匹配则复用，否则写入默认布局。

### CLI 说明

布局仅能通过 Web UI 编辑；CLI 会自动读取 `<work-dir>/layout.json`。手动编辑 JSON 后重跑 pipeline 即可生效：

```bash
python -m video_generator.run --task-dir tasks/<id> --format 16:9 \
  --work-dir tasks/<id>/video_16x9
```

删除 `layout.json` 可让 pipeline 重新生成默认布局。

---

## PDF 页面渲染

**模块：** `pdf_pages.py`

### 渲染参数

- 使用 PyMuPDF (`fitz`)，`zoom = dpi / 72.0` 矩阵缩放
- 输出 `page_0001.png`、`page_0002.png` … 至 `pages/` 目录
- 无 alpha 通道（`alpha=False`）

### References 截断

自动检测页面文本中是否出现章节标题：

- 英文：`References` / `Reference`（不区分大小写，整行匹配）
- 中文：`参考文献`

检测到后，**该页作为最后一页渲染**，其后附录/补充材料不再进入视频。例如 21 页 PDF 若在第 11 页出现 References，则只渲染 11 页。

可通过 `max_pages=N` 手动限制页数；若同时设置 `max_pages`，References 截断仅在未指定 `max_pages` 时生效。

### 画布适配（合成阶段）

**16:9（有 `layout.json`）：** PDF 页渲染到 **page 区域** 内（`_render_layout_page_clip()`：白底全画布 + overlay 定位 page 区域），不再整页铺满。

**9:16 或无布局：** `compose._scale_pad_filter()` 整页居中缩放：

```
scale=W:H:force_original_aspect_ratio=decrease,
pad=W:H:(ow-iw)/2:(oh-ih)/2:color=white,
setsar=1,format=yuv420p
```

页面等比缩小至目标框或画布内，宽或高贴边，居中 pad 白边。宽高强制为偶数（H.264 要求）。

---

## 页面时间轴

**模块：** `timeline.py`

### 权重分配

`build_equal_page_timeline(page_paths, audio_duration, first_page_weight=2.0)`：

- 权重向量：`[2.0, 1.0, 1.0, …, 1.0]`
- 每页时长 = `audio_duration × weight[i] / sum(weights)`
- 最后一页 `end` 强制对齐 `audio_duration`，消除浮点误差

示例：228s 音频、11 页 → 首页约 38s，其余每页约 19s。

### Concat demuxer

`write_concat_demuxer()` 生成 `pages.concat.txt`（供调试）；实际合成走 **逐页 clip + concat** 路径（见下文），避免单 concat 图片流换页卡住的问题。

---

## 字幕系统

字幕处理分为三阶段：**Round 级时间戳 → 子句展开 → ASS 渲染**。

### 1. Round 级时间戳（`podcast_timestamps.py`）

**默认来源：** `run.log`

解析规则：

```
[round start] Speaker: 文本内容
[round end] {'start_time': 0.0, 'end_time': 12.5, 'audio_duration': 12.5, ...}
```

流程：

1. 若存在 `transcript_timed.json`，直接加载
2. 否则解析 `run.log` 得到 `TimedRound` 列表
3. 若 `transcript.txt` 行数与 round 数一致，用 transcript 文本覆盖 log 中的文本（更准确）
4. 跳过 `round_id == 9999` 的条目（系统占位 round）
5. 写出 `subtitles.srt`、`subtitles.json`；若尚无 `transcript_timed.json` 则一并写出

**Whisper 备选（`align.py`）：**

| `--align-mode` | 行为 |
|----------------|------|
| `auto` | 优先 stable-ts `model.align()` 强制对齐；无 stable-ts 则 fallback whisper transcribe |
| `forced` | 仅 stable-ts，缺失则报错退出 |
| `transcribe` | 仅 whisper 转写，不依赖 transcript 文本对齐 |

对齐文本来自 `transcript_to_alignment_text()`：将所有 round 文本直接拼接（无空格）。

### 2. 子句展开（`clause_timestamps.py`）

每个 round 段落在写入 ASS 前经 `expand_segments_by_clauses()` 展开：

**切分：** 正则 `(?<=[，。])` — 在中文逗号、句号之后切分。

**短句合并：** `merge_short_clauses()`，相邻子句合并直到 **有效字符数 ≥ 10**（不计标点与空白）。避免 TTS 产生的 `嗯，` 等极短片段单独占屏。

**加权子时间戳：** `clause_weight()` 规则：

| 字符类型 | 权重 |
|----------|------|
| 普通可见字符 | 1 |
| `，,、；` 或 `。.!?！？` | 2 |
| 空白 | 0 |

在一个 round 的时间窗口内，按权重比例分配各子句的 `[start, end)`；最后一条子句 `end` 对齐 round 的 `end`。

**示例：**

```
Round: [10.0s, 20.0s] "方法很简单，效果也很好。"
  → [10.0, 14.4] "方法很简单，"
  → [14.4, 20.0] "效果也很好。"
```

### 3. 文本格式化（`subtitle_format.py`）

**normalize_flow_text()：**

- 句中 `。` 转为 `，`（TTS 停顿标点合并为逗号）
- 英文 `.` 后接非空白同样转 `，`
- 保护小数（如 `3.14`）不被误替换
- 合并连续逗号、压缩空白

**wrap_subtitle_text()：**

- 按 CJK / 拉丁 token 估算行宽（CJK=1.0，拉丁≈0.55）
- 优先在逗号处断行
- `_merge_orphan_lines()`：宽度过窄的孤行合并到相邻行
- 特殊规则：`是不是` 中的 `是` 不单独换到下一行
- `[CONTINUES]` 等 `[...]` token 作为整体不断开
- 单行有效字符 < 10 时与下一段合并后再 wrap

### 4. ASS 样式（`subtitle_style.py` + `align.py`）

每条字幕 cue 对应 **两条 ASS Dialogue**：

| Layer | 内容 |
|-------|------|
| 0 | 圆角矩形矢量背景（`\p1` 绘制） |
| 1 | 白色文本 |

背景样式：

- 颜色：黑色 `\c&H000000&`
- 透明度：30%（ASS alpha `\1a&H4D&`，即 77/255）
- 圆角半径：`min(font_size×0.28, box_h/4, box_w/4, 16)`

字体：`PingFang SC`，PrimaryColour 白色，Outline/Shadow 为 0（依赖底框而非描边）。

**淡入淡出：**

- 第一条 cue：`first_subtitle_fade_ms`（预设 280ms）
- 其余 cue：`subtitle_fade_ms`（默认 0；CLI `--subtitle-fade-ms` 可全局设置）
- 最小有效值 80ms

---

## 左侧记忆锚点（板书 Agent）

**仅 16:9 横屏**启用。竖屏（9:16）暂不添加。

### 设计目标

在 PDF 居中缩放后的 **左侧留白区**（不缩小 PDF、不覆盖页面）逐条显示 **4～6 个结构化记忆锚点**。每条含 **标题 + 补充说明**，在 Agent 指定时间出现后 **保留到视频结束**（累积显示，只增不减）。

### 结构范式

| 位置 | role | 内容 |
|------|------|------|
| 第 1 条 | `problem` | 背景或需要解决的核心问题 |
| 第 2～(N-1) 条 | `innovation` | 创新点 / 贡献 / 技术亮点（**至少 2 条、最多 4 条**） |
| 最后 1 条 | `result` | 实验结果、性能表现或关键发现 |

### 数据流

```
subtitles.json (round 级) + run.log 时间戳
        │
        ▼
key_points_agent.py  ──►  Ark LLM (ARK_API_KEY)
        │
        ▼
key_points.json  ──►  key_points_render.py  ──►  key_points.ass
        │
        ▼
merge_ass_files(subtitles.ass + key_points.ass)  ──►  video.ass
```

### Agent 行为（`key_points_agent.py`）

- **输入**：全部 round 级对话片段（`segment_index`、`start`、`end`、文本）
- **输出**：JSON，每条 `{ "role", "title", "detail", "segment_index" }`
- **数量**：4～6 条（1 problem + 2～4 innovation + 1 result）
- **title**：4～14 字短标题
- **detail**：15～25 字补充说明，对标题稍作展开
- **时间规则**：`segment_index` 严格递增
- **失败策略**：API 失败或左侧留白不足（<180px）时 **跳过板书**，无兜底

### 环境变量

复用 EssayPod 现有 Ark 配置（与 `condense_agent.py` 相同）：

| 变量 | 用途 |
|------|------|
| `ARK_API_KEY` | API 密钥（必填） |
| `ARK_BASE_URL` | 默认 `https://ark.cn-beijing.volces.com/api/v3` |
| `ARK_TEXT_MODEL` | 默认 `doubao-seed-1-6-250615` |
| `PODCAST_AGENT_TEMPERATURE` | 默认 `0.2` |

### 视觉样式（`key_points_render.py`）

- **有 `layout.json`：** 标题在 `title` 区域、板书在 `key_points` 区域内排版；宽度/高度由区域 bounds 决定，超出时自动压缩间距
- **无布局（9:16 回退）：** 左侧留白区内，距左缘 30px、距顶 132px；文本可延伸至 PDF 页内白边
- **无背景框**，仅保留蓝色 `▸` 三角标签
- 主标题：约 **42px**（1080p）加粗，第 **5 秒**出现，保留至结束
- 锚点标题：约 **39px** 加粗；补充说明：约 **33px**，最多 2 行
- **6 条自适应**：超出区域高度时自动压缩条目间距
- 动画：500ms fade in；ASS **Layer 2**（位于 PDF 像素层之上）
- 补充说明出现时机：当前标题与下一标题时间间隔的 **1/3** 处

### 布局计算

**区域模式（16:9）：** 见 [画面布局编辑器](#画面布局编辑器169) 与 `video_layout.py`。

**legacy 模式（9:16）：** `layout.py` 计算左右留白：

```python
left_gutter = (canvas_width - scaled_page_width) // 2
```

其中 `scaled_page_width = page_width × min(canvas_width/page_width, canvas_height/page_height)`。

### 示例输出

`key_points.json`：

```json
[
  {
    "index": 1, "role": "problem",
    "title": "复杂文档检索难题", "detail": "传统RAG难以保留结构",
    "start": 17.746, "segment_index": 3
  },
  {
    "index": 2, "role": "innovation",
    "title": "视觉引导分块", "detail": "利用页面布局信息切分",
    "start": 81.432, "segment_index": 8
  },
  {
    "index": 3, "role": "innovation",
    "title": "多模态索引策略", "detail": "图文联合检索提升召回",
    "start": 120.5, "segment_index": 12
  },
  {
    "index": 4, "role": "result",
    "title": "准确率0.89", "detail": "显著优于基线方法",
    "start": 154.099, "segment_index": 14
  }
]
```

### CLI

```bash
# 默认 16:9 会自动调用板书 Agent
python -m video_generator.run --task-dir tasks/<id> --format 16:9

# 跳过 Agent，但复用已有 key_points.json
python -m video_generator.run --task-dir tasks/<id> --format 16:9 --skip-key-points

# 跳过右侧 PDF 图表提取
python -m video_generator.run --task-dir tasks/<id> --format 16:9 --skip-page-images
```

---

## 主标题与气泡音效（16:9）

**仅 16:9 横屏**启用。

### 主标题（`condensed.py` + `key_points_render.py`）

- 从 `<task-dir>/condensed.txt` 中解析 `【标题】` 段后的首行非空文本
- 第 **5 秒**以加粗字体出现在 **title 区域**内（默认布局为左侧顶部）
- 即使板书 Agent 失败，只要有主标题仍会写入 `key_points.ass` 并烧录进成片

### 气泡音效（`audio_effects.py`）

- 音效文件：`video_generator/data/bubble_sound.mp3`
- 在每条板书**标题**的 `start` 时间点混入播客音频
- 输出 `podcast_with_bubbles.mp3`；合成视频时优先使用该文件
- 若音效文件缺失或混音失败，回退使用原始 `podcast.mp3`

---

## 右侧 PDF 图表（16:9）

**仅 16:9 横屏**启用。在 **`figures` 布局区域**（默认位于 PDF 右侧）展示论文中的 Figure。

### 设计行为

| 行为 | 说明 |
|------|------|
| 提取对象 | 仅 **Figure / Fig.**，**不提取 Table** |
| 出现时机 | 离开该 PDF 页、切换到下一页时（`PageSlot.end`） |
| 持久显示 | 出现后保留至视频结束 |
| 多图排列 | 按出现顺序上下堆叠，自动压缩间距与尺寸防溢出 |
| 动画 | 自左向右滑入 + 淡入，时长 **1 秒** |
| 不遮挡 PDF | overlay 宽度等于 `figures` 区域，叠在区域原点，不覆盖 page 区域 |

### 提取策略（`pdf_images.py`）

1. **嵌入位图**：`page.get_images()` + `doc.extract_image(xref)`，按页内 bbox 排序
2. **矢量 Figure**（LaTeX 论文常见）：匹配 caption 正则 `^Figure/Fig. N`，裁剪 caption 上方区域；同页多图时以上一 caption 底部为下界，避免重复裁剪

每页若已有嵌入图则不再做 caption 裁剪，避免重复。

### 布局与合成（`page_images_render.py` + `compose.py`）

```
extract_pdf_page_images() ──► page_images/*.png
        │
build_page_image_events(slots) ──► page_images.json  (appear_at = 该页 end)
        │
plan_page_image_layout(figures_region=...) ──► figures 区域内 x/y/width/height
        │
render_page_image_overlays() ──► page_image_clips/*.mp4
        │
compose._build_overlay_filter_chain() ──► 叠在 page 之上、key_points.ass 之下
```

- 可用宽高：`figures` 区域减去内边距
- 滑入距离默认 108px（1080p 下 `SLIDE_PX` 按画布缩放）
- overlay 叠在 `figures.x / figures.y`，宽度等于 `figures.w`（偶数对齐）

### 示例 `page_images.json`

```json
[
  {
    "page_index": 3,
    "image_path": "page_images/page_0004_clip_01.png",
    "appear_at": 95.31,
    "caption": "Figure 1: Multimodal Document Chunking Architecture...",
    "native_width": 975,
    "native_height": 357
  }
]
```

### CLI

```bash
# 默认 16:9 会自动提取并叠加 Figure
python -m video_generator.run --task-dir tasks/<id> --format 16:9

# 跳过图表功能
python -m video_generator.run --task-dir tasks/<id> --format 16:9 --skip-page-images
```

---

## 视频合成

**模块：** `compose.py`

### 逐页 clip 策略

早期实现使用单路 ffmpeg concat 图片 + `-loop 1` 时，会出现 **画面长期停留在第 1 页** 的问题。当前方案：

1. 为每个 `PageSlot` 单独渲染 `clips/page_XXXX.mp4`（16:9 下 PDF 仅占据 **page 区域**）
2. 用 concat demuxer 拼接所有 clip
3. **[16:9]** 按时间叠加 **figures 区域** overlay clip（`page_image_clips/`）
4. 烧录 **key_points.ass**（标题 + 板书）
5. 烧录 **subtitles.ass**（底部字幕，最上层）
6. mux 音频（优先 `podcast_with_bubbles.mp3`）输出 MP4

### 右侧 Figure overlay

`_build_overlay_filter_chain()` 图层顺序：

```
[页面 concat] ──► figures overlay₁ @ t₁ ──► … ──► key_points.ass ──► subtitles.ass ──► [vout]
```

每个 overlay 输入使用 `-itsoffset` 对齐出现时间，位置为 `(figures.x, item.y)`。

### 最终 mux（无 overlay / 无板书时）

```bash
ffmpeg -f concat -safe 0 -i clips.concat.txt -i podcast.mp3 \
  -vf "subtitles='.../subtitles.ass'" \
  -c:v libx264 -crf 18 -pix_fmt yuv420p -c:a aac -b:a 192k \
  -shortest -movflags +faststart paper_video.mp4
```

有 overlay 或板书时使用 `-filter_complex`，映射 `[vout]` 与音频流。

常量：`FPS = 25`，`VIDEO_CRF = 18`，音频码率 192 kbps AAC。

### 字幕重烧（不重渲染页面）

`remux_video_with_subtitles(work_dir, audio_path, subtitle_path, output_path)`：在已有 `clips.concat.txt` 和 clip 文件的前提下，仅重新 mux + 烧字幕。**不含**右侧 Figure overlay 重烧；修改图表需完整重跑 pipeline。

### 首页 intro 动画

`_render_intro_clip()`，仅 `index == 0`：

| 阶段 | 时长 | 效果 |
|------|------|------|
| Intro | `min(1.0s, page_duration)` | zoompan：`z = 0.90 + 0.10 × progress`，居中；fade in 最多 0.75s |
| Hold | 剩余时长 | 静态 `_render_static_clip()` |

Intro 段使用 `-frames:v` 精确控制 zoompan 帧数（`intro_frames = round(intro_d × 25)`），避免 zoompan 默认长循环导致动画拖长。

其余页：纯静态 loop 图片 clip，25 fps。

---

## Web UI 集成

EssayPod 网页端（`app.py` + `static/index.html`）在播客生成完成后提供视频制作入口。

> **重做 Web UI？** 见 [`docs/UI_REDESIGN_HANDOFF.md`](../docs/UI_REDESIGN_HANDOFF.md) — 含完整 API 契约、layout.json schema、组件划分与验收清单，可直接交给 Codex 接手。

### 使用流程

1. 上传 PDF 并生成播客（`status = ready`）
2. 任务详情页 **「讲解视频」** 区块选择 **横屏 16:9** 或 **竖屏 9:16**
3. **（16:9）** 在 **「画面布局」** 中拖动/缩放四个区域，或点击「恢复默认布局」
4. 点击 **「开始制作视频」**，后台线程调用 `video_generator.pipeline.generate_video()`
5. 生成完成后页面内 **预览播放**，可 **下载 MP4**

### API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/tasks/{id}/video/generate` | 表单字段 `video_format=16:9` 或 `9:16` |
| `GET` | `/api/tasks/{id}/video?video_format=16:9` | 在线预览 |
| `GET` | `/api/tasks/{id}/video?video_format=16:9&download=1` | 下载 |
| `GET` | `/api/tasks/{id}/video/info?video_format=16:9` | 状态与文件大小 |
| `GET` | `/api/tasks/{id}/video/layout?video_format=16:9` | 读取/初始化布局（见 [画面布局编辑器](#画面布局编辑器169)） |
| `PUT` | `/api/tasks/{id}/video/layout` | 保存自定义布局 |
| `POST` | `/api/tasks/{id}/video/layout/reset` | 恢复默认布局 |

任务列表 API 返回 `video` 字段，包含两种画幅的 `status`（`idle` / `generating` / `ready` / `failed`）与 `ready` 布尔值。

### 输出路径

| 画幅 | work_dir | 输出文件 |
|------|----------|----------|
| 16:9 | `tasks/<id>/video_16x9/` | `paper_video.mp4` |
| 9:16 | `tasks/<id>/video_9x16/` | `paper_video.mp4` |

启动服务：

```bash
cd EssayPod
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

---

## 模块结构

```
video_generator/
├── __init__.py
├── __main__.py          # python -m video_generator 入口
├── run.py               # CLI argparse
├── pipeline.py          # 主编排 generate_video()
├── pdf_pages.py         # PDF → PNG，References 截断
├── timeline.py          # 页面时间槽 PageSlot
├── podcast_timestamps.py# run.log → round 字幕
├── clause_timestamps.py # round → 子句 + 加权时间
├── subtitle_format.py   # 文本规范化与换行
├── subtitle_style.py    # 圆角底框 ASS 矢量
├── align.py             # ASS/SRT/JSON 写入，Whisper 对齐
├── compose.py           # ffmpeg clip 渲染与 mux
├── transcript.py        # transcript.txt 解析
├── video_presets.py     # 16:9 / 9:16 预设（1080p）
├── video_layout.py      # 四区域 layout.json 读写与默认推导
├── layout.py            # 左右留白、PDF 页内白边、UI 缩放 helper
├── condensed.py         # 从 condensed.txt 解析主标题
├── audio_effects.py     # 板书气泡音效混音
├── ark_client.py        # Ark LLM HTTP 封装（复用 condense_agent）
├── key_points_agent.py  # 板书 Agent：提取记忆锚点
├── key_points_render.py # 左侧锚点 + 主标题 ASS 渲染与合并
├── pdf_images.py        # PDF Figure 提取与时间映射
├── page_images_render.py# 右侧图表布局与滑入 overlay clip
├── data/
│   └── bubble_sound.mp3 # 板书标题气泡音效
├── requirements.txt
└── README.md
```

### 核心数据结构

```python
@dataclass
class PageSlot:
    page_index: int
    start: float
    end: float
    image_path: Path

@dataclass
class SubtitleSegment:
    index: int
    start: float
    end: float
    text: str

@dataclass
class TimedRound:
    round_id: int | None
    speaker: str
    text: str
    start: float
    end: float

@dataclass
class PageImageEvent:
    page_index: int
    image_path: Path
    appear_at: float
    caption: str | None
```

Web 端编排见上级目录 `EssayPod/app.py`（`/api/tasks/{id}/video/*`）。

---

## 中间产物

调整样式或排查问题时，常见工作流：

```bash
# 仅重建 ASS（从已有 subtitles.json，不重跑 Whisper / PDF）
python -m video_generator.run --task-dir tasks/<id> --skip-align

# 若 clips 已存在，可在 Python 中调用：
# compose.remux_video_with_subtitles(work_dir, audio_path, subtitle_ass, output_path)
```

调试字幕时间轴：查看 `subtitles_clauses.json` 中每条 cue 的 `start` / `end` / `text`。

调试换页节奏：查看 `page_timeline.json` 中各页 `start` / `end`。

---

## 依赖与环境

### Python 包（`requirements.txt`）

| 包 | 必需 | 用途 |
|----|------|------|
| `pymupdf` | **是** | PDF 渲染 |
| `stable-ts` | 否 | Whisper 强制对齐 |
| `openai-whisper` | 否 | ASR 转写 / stable-ts 底层 |

默认 `--subtitle-source podcast` **只需 pymupdf**。

### 系统工具

- `ffmpeg` / `ffprobe`（PATH 可访问）
- macOS 推荐：`brew install ffmpeg`

### Whisper 环境说明

Whisper 依赖 PyTorch，本地可能存在 torch / torchaudio 版本不匹配问题。若 `--subtitle-source whisper` 失败，优先使用默认 podcast 时间戳；或单独修复 PyTorch 环境后再试。

首次使用 Whisper 会下载模型，体积取决于 `--whisper-model`（tiny < small < medium < large-v3）。

---

## 常见问题

### 画面一直停在第一页

请确认使用的是 **逐页 clip + concat** 版本的 `compose.py`，而非旧版单 concat 图片流。检查 `clips/` 目录是否有多段 `page_XXXX.mp4`。

### 字幕与音频不同步

1. 确认 `run.log` 中 `[round end]` 时间戳完整
2. 对比 `subtitles.json`（round 级）与 `subtitles_clauses.json`（子句级）时间
3. 子句切分仅在一个 round 内部按比例分配，**不会**跨 round 校正漂移

### References 之后的内容仍出现

检查 PDF 该页是否包含精确标题行 `References` 或 `参考文献`（整行匹配）。若标题写法不同，可在 `pdf_pages._REFERENCES_HEADING_RE` 中扩展正则。

### 竖屏字幕过大 / 溢出

9:16 预设已使用 40px 字号与更大底边距；若仍溢出，可在 `video_presets.py` 调整 `subtitle_margin_lr` 或 `subtitle_font_size`。

### 首页动画过长

Intro 硬上限 1s，zoompan 帧数由 `-frames:v` 锁定；若异常，检查 ffmpeg 版本是否支持所用 filter 表达式。

### 16:9 未出现右侧 Figure

1. 确认未加 `--skip-page-images`
2. 该页是否有 `Figure N` / `Fig. N` caption，或嵌入位图
3. **Table 不会被提取**；仅 Figure
4. LaTeX 矢量论文需 caption 上方有可检测的 drawing 区域
5. 查看 `page_images.json` 与 `page_images/` 目录

### 中间 PDF 页被白色 overlay 盖住

旧版曾用全画布白底 overlay；当前 `page_image_clips/` 宽度限制为 `right_gutter`，叠在 PDF 右边缘。若仍异常，删除旧 clip 后重跑 pipeline。

### Web UI 视频无法播放或页面闪烁

生成完成后不应再轮询刷新视频 DOM。若预览异常，硬刷新页面；重新制作视频会覆盖 `paper_video.mp4`。

### 布局编辑器里 PDF 盖住了标题/板书

编辑器预览按 z-index 叠层：page 在最底、title 在最上。若仍异常，硬刷新页面。

### 成片中标题/板书被 PDF 挡住

确认已用新版 pipeline（分两次烧 ASS：`key_points.ass` → `subtitles.ass`）。删除旧 `paper_video.mp4` 后重新生成；修改 `layout.json` 后需完整重跑，不能仅用 `remux_video_with_subtitles`。

### 修改布局后视频未变化

确认已保存布局（编辑器提示「布局已保存」），且 work_dir 下 `layout.json` 已更新。CLI 重跑：

```bash
python -m video_generator.run --task-dir tasks/<id> --format 16:9 \
  --work-dir tasks/<id>/video_16x9
```

---

## 后续扩展

| 方向 | 说明 |
|------|------|
| TTS 阶段写时间戳 | 在 `podcast_tts.py` 生成音频时直接输出 `transcript_timed.json`，减少对 `run.log` 解析的依赖 |
| 智能换页 | 当前为按权重均分；可改为按章节/音频段落对齐 PDF 页 |
| 9:16 布局编辑 | 竖屏四区域自定义布局 |
| 布局模板 | 保存/分享常用 layout preset |
| 云端字幕 API | 替换 `align.py` 中对齐实现，pipeline 其余部分不变 |
| Logo / 品牌 overlay | 右上角常驻 logo（资源：`data/logo.png`） |
| 图表与播客联动 | 根据 transcript 提及 Figure 的时间点触发右侧插图 |

---

## 示例任务

`tasks/11bc60b8846049568963b65e6dcc1ad1` 已验证：

- `video_16x9/paper_video.mp4` — 1920×1080 横屏（可拖拽布局 + 板书 + 主标题 + 气泡音 + Figure）
- `video_9x16/paper_video.mp4` — 1080×1920 竖屏
- `video_16x9/layout.json` — 默认四区域布局（可在 Web UI 修改）
- 21 页 PDF → References 截断后 11 页入镜
- 第 4 页 Figure 1 在切至第 5 页时滑入右侧空白区
