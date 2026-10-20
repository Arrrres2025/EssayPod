# EssayPod

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)

> [!IMPORTANT]
> EssayPod 是本地优先的研究工具。请勿提交 `.env`、上传的论文、生成的音视频或
> 其他包含个人/受版权保护内容的运行数据；这些路径已由根目录 `.gitignore` 排除。

EssayPod 是一个面向学术论文的 AI 阅读与内容生产平台。导入 PDF 后，系统会并行生成审稿人式导读、双语 Markdown 论文、流式播客及视频制作素材，并可在 Web Video Studio 中编排和导出论文讲解视频。

> 当前仓库仍处于本地开发阶段。任务、音频、翻译和视频文件默认保存在 `tasks/`，尚未接入账号、云存储或生产数据库。

## 功能总览

### 论文导入与项目库

- 上传本地 PDF，或输入可下载的 PDF / arXiv 链接。
- URL 导入会立即创建 `downloading` 任务并关闭弹窗；链接解析和 PDF 下载在后台
  运行，侧边栏与论文页显示 `创建任务 → 解析链接 → 下载 PDF → 启动处理` 阶段。
- arXiv 与 ACL Anthology 页面链接会去除 UTM 等追踪参数并直接规范化为 PDF URL；
  其他论文页再从 citation metadata 和下载链接中发现 PDF。
- PDF 下载使用独立的两次网络尝试（系统网络路径一次、无代理直连一次），不再
  复用 Ark 的三次长超时重试；失败任务可在论文页点击“重新下载”。
- 支持快速浏览、标准解读、完整内容三种内容密度。
- 文件提交后自动开始处理，无需点击播放按钮触发生成。
- 同一份 PDF 会在后台提交给内嵌 LLM Wiki，增量更新 Source、Entity、Concept、Synthesis 页面及它们之间的链接。
- 左侧项目库展示任务状态，支持搜索、切换和删除项目。
- 长文件名自动省略，避免撑破导入区域。
- 每个任务保留 PDF、生成状态、音频、逐字稿、翻译、媒体与视频产物。

### Knowledge Graph

- 左侧 `Knowledge Graph` 是 EssayPod 内的一级入口，右侧主区域直接切换为知识网络，不经过独立的 Wiki 首页。
- Graph 继续复用 `llm_wiki` 的 PixiJS + d3-force 视图、筛选器、缩放、拖动、详情预览与 Markdown 页面跳转。
- Graph API 每次从当前 Markdown Wiki 派生节点与边；EssayPod 内嵌页面每 5 秒检查一次变化，论文编译完成后无需刷新整个应用。
- `llm_wiki` 原有的 `8765` 独立 Web UI 保留，可用于调试、Lint、查询和 Web Research。

### 审稿人导读

- 使用独立 LLM Agent 流式生成 Markdown，边生成边渲染。
- 不采用普通摘要式提问，而是围绕以下问题组织内容：
  1. 论文真正解决什么问题；
  2. 作者声称的贡献是否成立；
  3. 证据强度与关键实验；
  4. 最容易被审稿人攻击的短板；
  5. 哪些内容值得精读、哪些可以跳过。
- 支持标题、引用、重点、列表和结构化章节。
- 从全局 YOLO 媒体清单中选择前两张候选总览/架构图，分别插入问题和贡献部分。
- 图片异步就绪后会自动加载到预留位置。

### 流式播客

- PDF 文本先经过论文浓缩 Agent，再提交播客生成服务。
- 上传后自动生成；音频可收听之前，播放按钮保持禁用状态。
- 音频流达到约 10 秒缓存后即可提前开始收听，无需等待整段生成结束。
- 支持播放、暂停、15 秒前后跳转、拖动进度条、音量和倍速。
- 直播音频切换为最终 MP3 时保持当前播放位置与播放状态，不打断收听。
- 支持 checkpoint、partial 音频和中断恢复。
- 保存逐轮文本及精确时间戳；页面字幕随当前播放时间更新。
- 播客播放与视频生成相互独立，制作视频不会暂停音频。

### 论文全文翻译

- 使用 `translation_block` 的 YOLO 版面识别恢复论文阅读顺序。
- 识别标题、正文、Figure、Table、独立公式及其 caption。
- 图、表、公式以图片形式保留，文字块使用 Google 免费翻译并行翻译。
- 默认使用 4 个翻译线程，输出：
  - `input.md`：原文 Markdown；
  - `input.zh.md`：中文 Markdown；
  - `assets/`：图、表和公式裁图；
  - `media_manifest.json`：全局媒体清单。
- Markdown 页面支持标题、段落、列表、图片、表格截图和公式截图。
- 原文/中文切换器在翻译卡片内吸顶，并保持相同的相对阅读进度。
- 最大图片与正文同宽，其他媒体按原始尺寸比例统一缩放。
- 相邻两个媒体缩放后可放入正文宽度时自动并排；说明文字仍在图片行下方使用整行宽度。

### 全局 YOLO 媒体流水线

- 导入论文时由翻译流水线统一执行 YOLO 版面检测与媒体裁剪。
- 摘要、论文翻译和视频制作共享同一个 `media_manifest.json` 与 `assets/`。
- Figure、Table、Formula 使用 216 DPI 高清裁剪。
- 保存媒体类型、页码、PDF bbox、Figure 编号、caption、置信度与资源路径。
- 如果检测到图注但漏掉对应媒体，会在本地以 2× 分辨率执行一次补检。
- 新任务不再运行旧的“本地分析 + 远端 OCR”图片流水线。
- 旧任务仍可只读兼容 `ocr_block` 的历史产物。

### Video Studio

- 支持横屏 `16:9`（1920×1080）和竖屏 `9:16`（1080×1920）。
- 16:9 画布包含标题、记忆板书、PDF 页面、Figure 图表和底部字幕。
- 四个主要区域支持拖动和缩放，并有水平/垂直中心线吸附。
- 支持图层上移、下移，控制标题、板书、PDF 和图表遮挡顺序。
- 字幕支持垂直位置和字号滑杆，画布实时预览；字号范围与实际渲染上限一致。
- PDF 区域支持裁切式展示：区域比例较扁时可只显示部分页面，视频中自动向下滚动并在换页前到达底部。
- 推荐布局为默认布局；支持最多 3 个跨任务模板。
- “保存为模板”可供其他论文的 16:9 Studio 使用；“保存布局”仅保存当前论文布局。
- 画布下方显示推荐布局、模板 1～3、保存模板和保存布局。
- 视频生成按钮位于右上角，Header 下方细进度条展示实时阶段和百分比。
- 视频完成后可直接预览和下载。

### 视频生成行为

- 渲染 PDF 全部页面，不在 References 页提前截断。
- 页面时间轴覆盖完整播客时长，首页约为普通页面的 2 倍时长。
- 第一页带放大与淡入动画，其他页面按时间轴切换。
- Figure 区域是当前页面的临时媒体舞台，不再累计展示全部图片：
  - 当前 PDF 页面出现约 1/4 后，属于该页的媒体渐入；
  - 媒体始终等比例限制在 Figure 区域内；
  - 页面切换前渐出；
  - 同页多张媒体在该页剩余时段内依次展示；
  - 媒体结束后主视频继续播放，不会被 overlay 截断。
- 播客时间戳会展开为子句级字幕，按标点分配时间。
- 支持 ASS 字幕、圆角半透明背景、主标题、记忆锚点和提示音效。
- 可选 Whisper / stable-ts 对齐作为播客时间戳的备选方案。

## 处理架构

```text
                         ┌─ Reviewer Brief Agent ──► brief.md (SSE 流式输出)
PDF / PDF URL ─► Task ──┼─ Podcast Agent + TTS ───► partial/final MP3 + transcript
                         ├─ YOLO Document Pipeline ─► input.md + media_manifest + assets
                                                        │
                                                        ├─ Google 多线程翻译 ─► input.zh.md
                                                        ├─ 摘要插图
                                                        └─ Video Studio Figure 素材
                         └─ LLM Wiki Compiler ──────► Markdown Wiki ─► Knowledge Graph

PDF pages + podcast + transcript + layout + media
                         └─► FFmpeg video pipeline ─► paper_video.mp4
```

URL 导入先立即创建任务，再在后台解析和下载 PDF；PDF 验证并原子落盘后，摘要、
播客、YOLO/翻译和 LLM Wiki 流水线才并行运行。Wiki 编译任务使用单独的后台线程，
并串行修改同一个知识库，避免并发上传相互覆盖；它不会阻塞论文上传响应或其他
EssayPod 产物生成。

## 技术栈

- 后端：Python、FastAPI、Uvicorn
- 前端：React 19、TypeScript、vinext / Vite、Tailwind CSS
- PDF：PyMuPDF、pypdf
- 版面检测：YOLO ONNX / ONNX Runtime
- 文本 Agent：火山方舟 Ark（OpenAI-compatible API）
- 翻译：Google 免费翻译接口，4 线程
- 音频：火山语音播客 WebSocket 接口
- 视频：FFmpeg / ffprobe、ASS 字幕
- 可选字幕对齐：Whisper / stable-ts

## 目录结构

```text
EssayPod/
├── app.py                     # FastAPI API、任务调度、音视频接口
├── llm_wiki_bridge.py         # 上传任务到内嵌 Wiki 的后台桥接与 Graph/Page 路由适配
├── llm_wiki/                  # Karpathy 三层结构、独立 UI、Graph 与持久 Wiki 数据
├── podcast_tts.py             # 流式播客客户端与 checkpoint
├── condense_agent.py          # 播客讲解稿浓缩 Agent
├── brief_agent.py             # 审稿人导读流式 Agent
├── pdf_fetch.py               # PDF URL / arXiv 下载
├── translation_block/         # YOLO 文档转 Markdown 与多线程翻译
├── video_generator/           # 页面、字幕、媒体动画与 FFmpeg 合成
├── web_ui/                    # React / vinext Web UI
├── ocr_block/                 # 历史 OCR 流程，仅兼容旧任务
├── docs/                      # 产品与 UI 设计说明
├── static/                    # 后端静态资源
├── tasks/                     # 本地任务和生成产物（运行时目录）
└── outputs/                   # CLI 默认输出目录
```

## 环境要求

- Python 3.11（当前开发环境）
- Node.js `>=22.13.0`
- FFmpeg 与 ffprobe
- macOS / Linux；Windows 需自行适配 FFmpeg 和字体路径

### 安装系统依赖

macOS：

```bash
brew install ffmpeg
```

### 安装 Python 依赖

```bash
cd EssayPod
python -m venv ../.venv
source ../.venv/bin/activate
pip install -r requirements.txt
pip install -r video_generator/requirements.txt
pip install -e translation_block
```

`translation_block` 包含额外的 ONNX、Markdown 导出和翻译依赖，因此不能只安装根目录的 `requirements.txt`。

接着创建本地配置文件：

```bash
cp .env.example .env
```

编辑 `.env`，只填写你实际使用的服务密钥。`.env` 已被 Git 忽略，禁止将真实密钥
写进源码或提交到 GitHub。

### 安装前端依赖

```bash
cd EssayPod/web_ui
npm install
```

## 环境变量

项目会读取工作区或 EssayPod 目录下的 `.env`。常用配置：

```env
# 播客服务（PODCAST_API_KEY 与 SEED_AUDIO_API_KEY 二选一）
PODCAST_API_KEY=
SEED_AUDIO_API_KEY=
PODCAST_ENDPOINT=
PODCAST_APP_ID=
PODCAST_ACCESS_KEY=
PODCAST_RESOURCE_ID=
PODCAST_APP_KEY=
PODCAST_SPEAKER_A=
PODCAST_SPEAKER_B=

# Ark：摘要、浓缩与视频板书 Agent
ARK_API_KEY=
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_TEXT_MODEL=
TAVILY_API_KEY=                # 缺失背景概念的自动 Web Search

# 可选 Agent 参数
PODCAST_AGENT_TEMPERATURE=0.2
PODCAST_AGENT_MAX_TOKENS=4096
PODCAST_AGENT_TIMEOUT=360

# Web UI 后端地址（在 web_ui 构建环境中设置）
NEXT_PUBLIC_API_BASE=http://127.0.0.1:8010
```

也兼容部分历史变量：`DOUBAO_TTS_APPID`、`DOUBAO_TTS_ACCESS_TOKEN`、`PODCAST_ACCESS_KEY`、`PODCAST_RETRY_TASK_ID` 和 `PODCAST_LAST_FINISHED_ROUND_ID`。

论文翻译当前固定使用 Google 免费翻译，不需要 Ark 翻译密钥。

## 本地启动

### 1. 启动后端

```bash
cd EssayPod
../.venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8010
```

后端地址：`http://127.0.0.1:8010`

### 2. 启动 Web UI

```bash
cd EssayPod/web_ui
npm run dev
```

浏览器打开：`http://localhost:3000/`

### 3. 构建检查

```bash
cd EssayPod/web_ui
npm run build
```

## Web 使用流程

1. 点击“导入新论文”。
2. 选择本地 PDF 或输入论文链接，并选择处理密度。
3. 点击“导入并开始”。
4. 系统自动并行生成播客、审稿人导读、YOLO 媒体、双语论文和 LLM Wiki。
5. 播客缓存达到可播放阈值后，用户自行点击播放。
6. 在“摘要 / 论文翻译 / 视频工作室”之间切换；页面会尽可能保持当前滚动位置。
7. 在 Video Studio 调整布局、字幕、层级或套用模板。
8. 点击“生成视频”，查看进度，完成后预览或下载 MP4。
9. 点击左侧 `Knowledge Graph`，在 EssayPod 主区域浏览整个 Wiki；新论文的页面在后台编译完成后自动出现。

## 主要 API

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/tasks` | 任务列表 |
| `POST` | `/api/tasks` | 上传 PDF |
| `POST` | `/api/tasks/from-url` | 从 URL 创建任务 |
| `POST` | `/api/tasks/{id}/download/retry` | 重新启动失败或中断的 URL 下载 |
| `GET` | `/api/tasks/{id}` | 任务详情与各流水线状态 |
| `DELETE` | `/api/tasks/{id}` | 删除任务及本地产物 |
| `POST` | `/api/tasks/{id}/generate` | 启动/恢复播客生成 |
| `POST` | `/api/tasks/{id}/pause` | 暂停生成中的播客 |
| `GET` | `/api/tasks/{id}/wiki` | 当前论文的 Wiki 后台任务状态 |
| `GET` | `/api/wiki/status` | 全局 Wiki 与 EssayPod 编译队列状态 |
| `GET` | `/api/wiki/graph` | 从当前 Markdown Wiki 派生 Graph JSON |
| `GET` | `/api/wiki/graph-view` | EssayPod 内嵌 Graph 页面 |
| `GET` | `/api/wiki/page?path=...` | 渲染指定 Wiki Markdown 页面 |
| `GET` | `/api/wiki/page-view?path=...` | EssayPod 内嵌 Wiki 详情页 |
| `GET` | `/api/tasks/{id}/pdf` | 原始 PDF |
| `GET` | `/api/tasks/{id}/audio` | 最终音频，支持 Range |
| `GET` | `/api/tasks/{id}/audio-live` | 生成中的可播放音频 |
| `GET` | `/api/tasks/{id}/audio-chunk` | 音频分块 |
| `GET` | `/api/tasks/{id}/transcript-timed` | 带时间戳逐字稿 |
| `GET` | `/api/tasks/{id}/brief` | 导读状态与 Markdown |
| `GET` | `/api/tasks/{id}/brief/stream` | 导读 SSE 流 |
| `GET` | `/api/tasks/{id}/translation` | 原文/中文 Markdown |
| `GET` | `/api/tasks/{id}/translation/assets/{path}` | 翻译媒体资源 |
| `GET` | `/api/tasks/{id}/figures` | 全局 Figure 清单 |
| `GET` | `/api/tasks/{id}/figures/{figure_id}` | Figure 图片 |
| `GET/PUT` | `/api/tasks/{id}/video/layout` | 读取/保存视频布局 |
| `POST` | `/api/tasks/{id}/video/generate` | 生成视频 |
| `GET` | `/api/tasks/{id}/video/info` | 视频进度与状态 |
| `GET` | `/api/tasks/{id}/video` | 预览/下载最终视频 |

## CLI：播客

直接生成播客：

```bash
python podcast_tts.py \
  --pdf-file /path/to/paper.pdf \
  --condense-agent \
  --agent-input-chars 100000 \
  --condense-level mid \
  --transcript-out outputs/paper.transcript.txt \
  --out outputs/paper.mp3
```

只生成浓缩稿：

```bash
python condense_agent.py \
  --pdf-file /path/to/paper.pdf \
  --max-input-chars 100000 \
  --condense-level mid \
  --out outputs/paper_condensed.txt
```

断点恢复：

```bash
python podcast_tts.py \
  --pdf-file /path/to/paper.pdf \
  --condense-agent \
  --resume-partial \
  --resume-checkpoint \
  --out outputs/paper.mp3
```

## CLI：视频

```bash
python -m video_generator.run \
  --task-dir tasks/<task-id> \
  --format 16:9 \
  --work-dir tasks/<task-id>/video_16x9 \
  --output tasks/<task-id>/video_16x9/paper_video.mp4
```

常用参数：

- `--format 16:9|9:16`
- `--pdf-dpi 200`
- `--max-pages N`：仅用于快速测试；Web 正式生成不限制页数
- `--subtitle-source podcast|whisper`
- `--whisper-model small`
- `--skip-align`
- `--skip-key-points`
- `--skip-page-images`

## 任务目录

典型任务结构：

```text
tasks/<id>/
├── metadata.json
├── llm_wiki.json              # 该论文的 queued/running/completed/failed 状态与页面路径
├── input.pdf
├── condensed.txt
├── transcript.txt
├── transcript_timed.json
├── checkpoint.json
├── podcast.mp3.partial
├── podcast.mp3
├── brief.partial.md
├── brief.md
├── translation/
│   ├── input.md
│   ├── input.zh.md
│   ├── media_manifest.json
│   ├── assets/
│   └── run.log
├── video_16x9/
│   ├── layout.json
│   ├── page_timeline.json
│   ├── page_images.json
│   ├── subtitles.ass
│   ├── video_run.log
│   └── paper_video.mp4
└── video_9x16/
    └── paper_video.mp4
```

## 测试与验证

```bash
# Python 语法检查示例
python -m py_compile app.py brief_agent.py video_generator/pipeline.py

# 前端构建与渲染测试
cd web_ui
npm run build
npm test
```

## macOS 桌面应用

本地依赖安装完成后，可以生成连接当前源码目录的 macOS 应用壳：

```bash
./desktop/build-macos-app.sh
```

生成结果位于 `dist/EssayPod.app`。这是面向本机开发使用的临时签名版本；向其他
Mac 用户分发仍需要独立打包依赖、Developer ID 签名和 Apple 公证。详见
[`desktop/README.md`](desktop/README.md) 与
[`MACOS_APP_TECHNICAL_DEVELOPMENT.md`](MACOS_APP_TECHNICAL_DEVELOPMENT.md)。

视频相关问题可优先检查：

- `tasks/<id>/video_16x9/video_run.log`
- `tasks/<id>/video_16x9/page_timeline.json`
- `tasks/<id>/video_16x9/page_images.json`
- 系统中的 `ffmpeg` / `ffprobe` 是否可用

## 当前限制

- 数据仅保存在本机文件系统，没有用户隔离和云端持久化。
- Google 免费翻译可能受网络、频率限制或服务策略变化影响。
- YOLO 对极端版式仍可能漏检或误分图表；当前只有图注驱动的本地二次补检。
- 播客与 Ark Agent 依赖外部服务及有效凭据。
- 视频生成是 CPU / FFmpeg 密集任务，长播客与高页数论文耗时较长。
- 竖屏模式目前不提供完整的四区域自由布局编辑能力。
- 旧任务可能继续读取历史 `ocr_block` 数据；重新导入才能完整使用统一 YOLO 流水线。

## 相关文档

- [`translation_block/MARKDOWN_EXPORT_PIPELINE.md`](translation_block/MARKDOWN_EXPORT_PIPELINE.md)：论文转 Markdown 与翻译设计
- [`translation_block/README.md`](translation_block/README.md)：翻译模块上游说明
- [`video_generator/README.md`](video_generator/README.md)：视频模块细节（部分描述可能落后于主 README）
- [`docs/`](docs/)：UI 与产品设计说明

## 参与贡献

欢迎提交 Issue 和 Pull Request。开始前请阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)。
报告可能导致密钥或本地数据泄露的安全问题时，请阅读 [`SECURITY.md`](SECURITY.md)，
不要直接公开漏洞细节。

## 开源许可

EssayPod 以 **GNU Affero General Public License v3.0 or later** 发布。任何通过网络
向用户提供修改版 EssayPod 服务的人，也必须按 AGPL 要求向这些用户提供对应源码。
完整说明见 [`LICENSE`](LICENSE)，第三方项目声明见
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。
