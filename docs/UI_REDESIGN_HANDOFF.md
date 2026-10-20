# EssayPod Web UI 重做 — 衔接说明

> **读者**：接手重做前端 UI 的开发者或 Codex Agent。  
> **目标**：在不改动视频 pipeline 的前提下，替换 `static/index.html`，或拆成独立 SPA，仍与现有 FastAPI 后端完整对接。  
> **原则**：UI 与渲染解耦；**唯一布局契约是 `layout.json` + layout API**。

---

## 1. 系统分层

```
┌──────────────────────────────────────────────────────────────┐
│  新 Web UI（任意框架：React / Vue / 原生 HTML）                 │
│  职责：任务管理、播客播放、16:9 布局编辑、视频触发与预览          │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP（本文档定义的 API）
┌───────────────────────────▼──────────────────────────────────┐
│  EssayPod/app.py（FastAPI）                                   │
│  职责：任务 CRUD、播客生成调度、layout 读写、视频生成后台线程     │
└───────────────────────────┬──────────────────────────────────┘
                            │ Python import / 文件读写
┌───────────────────────────▼──────────────────────────────────┐
│  video_generator/（渲染引擎，UI 无关）                          │
│  pipeline.py → compose.py / key_points_render.py / ...        │
│  读 tasks/<id>/video_16x9/layout.json 出片                     │
└──────────────────────────────────────────────────────────────┘
```

**重做 UI 时通常只需改最上层**；`app.py` 的 API 与 `video_generator/` 可保持不变。

---

## 2. 关键文件

| 路径 | 作用 | UI 重做时 |
|------|------|-----------|
| `app.py` | 全部 REST API | 一般不改；SPA 需加 CORS |
| `static/index.html` | 当前单体 UI（含布局编辑器） | **替换或废弃** |
| `video_generator/video_layout.py` | `layout.json` 模型与校验 | 不改 |
| `video_generator/pipeline.py` | 视频生成入口 | 不改 |
| `video_generator/README.md` | pipeline 与布局功能文档 | 参考，非 UI 契约 |
| `tasks/<task_id>/metadata.json` | 任务状态（含 video 子状态） | 只读 |
| `tasks/<task_id>/video_16x9/layout.json` | 16:9 布局持久化 | 经 API 读写 |

**当前 UI 参考实现**（布局编辑器逻辑）：

- `static/index.html` 约 L587–1178：`layoutEditorState`、拖拽、保存、视频生成衔接
- `static/index.html` 约 L895–943：视频区块 HTML 结构
- `app.py` L557–774：layout 与 video 相关 API

---

## 3. 任务目录结构

每个任务在 `tasks/<task_id>/`：

```
tasks/<task_id>/
├── metadata.json          # 任务元数据 + video 生成状态
├── input.pdf              # 原始 PDF
├── condensed.txt          # 浓缩稿（含【标题】【关键信息核对】等）
├── transcript.txt         # 播客逐字稿
├── podcast.mp3            # 成品音频
├── podcast.mp3.partial    # 生成中 partial 音频
├── checkpoint.json        # 播客断点续传
├── run.log                # 播客生成日志
├── video_16x9/            # 横屏工作目录
│   ├── layout.json        # ★ 布局契约（16:9 自定义布局）
│   ├── paper_video.mp4    # 成品视频
│   ├── key_points.json    # 板书 Agent 输出
│   ├── pages/             # PDF 页 PNG
│   ├── page_images/       # Figure 裁剪 PNG
│   └── video_run.log      # 视频生成日志
└── video_9x16/            # 竖屏（无自定义 layout）
    └── paper_video.mp4
```

---

## 4. 任务对象（`public_task` 响应）

`GET /api/tasks` 与 `GET /api/tasks/{id}` 返回同一 schema。

### 4.1 核心字段

```json
{
  "id": "c84f44155bb94afcbbd05fc3c849695f",
  "title": "2607.12790",
  "filename": "2607.12790.pdf",
  "source_url": "https://arxiv.org/pdf/2607.12790.pdf",
  "condense_level": "low",
  "status": "ready",
  "error": "",
  "created_at": 1784212958.45,
  "updated_at": 1784265650.98,

  "display_title": "从 condensed.txt 解析的展示标题",
  "condensed_key_info": "【关键信息核对】段落全文",

  "pdf_ready": true,
  "audio_ready": true,
  "partial_ready": false,
  "condensed_ready": true,
  "transcript_ready": true,
  "checkpoint_ready": true,

  "video": {
    "16:9": {
      "format": "16:9",
      "status": "idle | generating | ready | failed",
      "ready": true,
      "error": "",
      "updated_at": 1784265650.98
    },
    "9:16": { "...": "同上" }
  },

  "log_tail": "run.log 末尾约 6000 字符"
}
```

### 4.2 播客 `status` 枚举

| status | 含义 | UI 建议 |
|--------|------|---------|
| `uploaded` | 已上传，未生成 | 显示「收听」 |
| `generating` | 播客生成中 | 禁用主按钮；可流式播放 partial |
| `pausing` | 正在暂停 | 显示暂停中 |
| `paused` | 已暂停 | 显示「继续生成」 |
| `ready` | 播客完成 | 显示播放器 + 视频区块 |
| `failed` | 失败 | 展示 `error` |

### 4.3 视频 `video[format].status`

| status | 含义 |
|--------|------|
| `idle` | 未生成或尚无有效 mp4 |
| `generating` | 后台线程运行中 |
| `ready` | `paper_video.mp4` 存在且非空 |
| `failed` | 生成失败，见 `error` |

**注意**：若磁盘上已有 mp4 且 status 不是 `generating`，后端会把 status 视为 `ready`（见 `app.py` `read_video_state`）。

---

## 5. REST API 完整清单

Base URL：开发时通常为 `http://127.0.0.1:8000`（以实际启动端口为准）。

### 5.1 页面与静态资源

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 返回当前 `static/index.html` |
| GET | `/static/*` | 静态资源 |

### 5.2 任务 CRUD

| 方法 | 路径 | Body | 响应 |
|------|------|------|------|
| GET | `/api/tasks` | — | `Task[]`，按 `created_at` 降序 |
| POST | `/api/tasks` | `multipart`: `file` (PDF), `condense_level` (`low\|mid\|high`) | `Task` |
| POST | `/api/tasks/from-url` | `form`: `pdf_url`, `condense_level` | `Task` |
| GET | `/api/tasks/{id}` | — | `Task` |
| GET | `/api/tasks/{id}/pdf` | — | PDF 文件流（inline） |

### 5.3 播客生成与播放

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks/{id}/generate` | 启动/继续播客生成（后台线程） |
| POST | `/api/tasks/{id}/pause` | 请求暂停播客生成 |
| GET | `/api/tasks/{id}/audio` | 成品或 partial MP3 |
| GET | `/api/tasks/{id}/audio-info` | `{ status, size, audio_ready, partial_ready }` |
| GET | `/api/tasks/{id}/audio-chunk?offset=N` | 流式增量音频（见 §6.2） |
| GET | `/api/tasks/{id}/condensed` | 浓缩稿纯文本 |
| GET | `/api/tasks/{id}/transcript` | 逐字稿纯文本 |

### 5.4 视频 — 布局（仅 16:9）

| 方法 | 路径 | Body / Query | 响应 |
|------|------|--------------|------|
| GET | `/api/tasks/{id}/video/layout` | `?video_format=16:9` | §5.5 |
| PUT | `/api/tasks/{id}/video/layout` | JSON §5.6 | `{ ok, layout }` |
| POST | `/api/tasks/{id}/video/layout/reset` | `form`: `video_format=16:9` | `{ ok, layout }` |
| GET | `/api/tasks/{id}/video/layout/preview-page` | `?video_format=16:9` | PNG（PDF 第一页） |
| GET | `/api/tasks/{id}/video/layout/preview-figure` | `?video_format=16:9&name=xxx.png` | PNG（Figure 预览） |

非 16:9 调用 layout API 会返回 **400**：`Custom layout is only supported for 16:9`。

### 5.5 GET layout 响应示例

```json
{
  "layout": {
    "canvas_width": 1920,
    "canvas_height": 1080,
    "regions": {
      "title":      { "x": 45,  "y": 72,  "w": 596, "h": 126 },
      "key_points": { "x": 45,  "y": 198, "w": 596, "h": 798 },
      "page":       { "x": 542, "y": 0,   "w": 834, "h": 1080 },
      "figures":    { "x": 1377,"y": 198, "w": 542, "h": 798 }
    }
  },
  "preview": {
    "title": "论文主标题（来自 condensed.txt）",
    "key_points": [
      { "title": "记忆锚点 1", "detail": "补充说明" }
    ],
    "page_url": "/api/tasks/{id}/video/layout/preview-page?video_format=16:9",
    "figure_urls": [
      "/api/tasks/{id}/video/layout/preview-figure?video_format=16:9&name=page_0003_fig_1.png"
    ]
  },
  "page_size": { "width": 1700, "height": 2200 }
}
```

- `preview.key_points`：最多 4 条，来自已有 `key_points.json`；若无则为 `[]`，UI 应显示占位文案。
- `preview.page_url`：无 PDF 时为 `null`。
- `preview.figure_urls`：最多 3 张；视频未跑过 pipeline 时可能为空。

### 5.6 PUT layout 请求体

```json
{
  "video_format": "16:9",
  "layout": {
    "canvas_width": 1920,
    "canvas_height": 1080,
    "regions": {
      "title":      { "x": 0, "y": 0, "w": 734, "h": 126 },
      "key_points": { "x": 0, "y": 126, "w": 698, "h": 954 },
      "page":       { "x": 496, "y": 0, "w": 816, "h": 1080 },
      "figures":    { "x": 1263, "y": 0, "w": 656, "h": 1080 }
    }
  }
}
```

后端会调用 `layout_from_dict()` + `clamp_to_canvas()` 校验并写入 `tasks/<id>/video_16x9/layout.json`。

### 5.7 视频 — 生成与预览

| 方法 | 路径 | 前置条件 | 说明 |
|------|------|----------|------|
| POST | `/api/tasks/{id}/video/generate` | `audio_ready` + `transcript_ready` + 存在 `input.pdf` | `form`: `video_format=16:9\|9:16` |
| GET | `/api/tasks/{id}/video/info` | — | `{ format, status, ready, error, size }` |
| GET | `/api/tasks/{id}/video` | mp4 已就绪 | `?video_format=16:9`；`&download=1` 触发下载 |

**生成前**：16:9 且布局有未保存修改时，**必须先 PUT layout**（当前 UI 在 `startVideoGeneration` 里自动保存）。

---

## 6. 功能模块衔接指南

### 6.1 模块划分（建议组件边界）

```
App
├── TaskSidebar          ← GET /api/tasks
├── TaskUpload           ← POST /api/tasks, /from-url
├── PodcastPanel         ← generate / pause / audio / 关键信息
├── VideoPanel
│   ├── FormatSelector   ← 16:9 | 9:16
│   ├── LayoutEditor     ← 仅 16:9；四区域 drag/resize
│   ├── VideoActions     ← generate / download
│   └── VideoPreview     ← <video src=".../video?...">
└── PollingHook          ← 统一轮询任务状态
```

### 6.2 播客流式播放（可选，当前 UI 已实现）

生成中且浏览器支持 `MediaSource` 时：

1. `GET /api/tasks/{id}/audio-chunk?offset=0`
2. 读响应头：`X-Next-Offset`, `X-Audio-Size`, `X-Audio-Complete`
3. append 到 SourceBuffer，继续请求下一 offset
4. `X-Audio-Complete: true` 且最终 mp3 就绪后切到普通 `<audio src=".../audio">`

不支持 MSE 时降级为 partial/final 的 `<audio src>`。

### 6.3 16:9 布局编辑器 — 必须遵守的契约

#### Region 名称（固定，不可改名）

| key | 中文标签 | 成片内容 |
|-----|----------|----------|
| `title` | 标题 | 论文主标题 ASS |
| `key_points` | 板书 | `▸` 记忆锚点 + 说明 ASS |
| `page` | PDF 页面 | PDF 页缩放嵌入 |
| `figures` | 图表 | Figure PNG 堆叠 |

#### 坐标系

- **逻辑画布**：1920 × 1080（与 `video_presets.py` 16:9 preset 一致）
- **显示缩放**：`displayScale = containerWidth / 1920`（当前 UI 默认 0.5）
- **保存前**：所有 `x/y/w/h` 必须是逻辑像素，不是 CSS 像素

#### 约束（前后端一致）

- 最小区域：`80 × 80`（`MIN_REGION_SIZE`）
- `w`、`h` 建议为**偶数**（ffmpeg 要求；后端 `_even()` 会自动修正）
- 区域不可超出 `[0, canvas_width)` × `[0, canvas_height)`

#### 预览 z-index（仅 UI 层叠，便于编辑）

从底到顶：`page(1) → figures(2) → key_points(3) → title(4)`

#### 交互（当前实现）

- **移动**：拖 region 标题栏（`.layout-region-header`）
- **缩放**：拖右下角 handle（`.layout-resize-handle`）
- **保存**：mouseup 后 debounce 立即 PUT（可改为显式「保存」按钮）
- **重置**：POST `/video/layout/reset`

#### 不属于 layout 的部分

- **底部字幕**：由 `subtitles.ass` 单独渲染，全宽底部居中，**不在四区域内**

### 6.4 视频生成流程

```
用户选择 format
    │
    ├─ 16:9 ─→ GET /video/layout（加载编辑器 + preview）
    │          用户编辑 → PUT /video/layout
    │          POST /video/generate?video_format=16:9
    │
    └─ 9:16 ─→ 跳过 layout API
               POST /video/generate?video_format=9:16

轮询 GET /api/tasks 或 GET /api/tasks/{id}
    直到 task.video[format].status ∈ { ready, failed }

ready → <video src="/api/tasks/{id}/video?video_format=16:9&t={timestamp}">
        （重生成后加 cache-bust 查询参数）
```

### 6.5 轮询策略（当前 UI）

- 间隔：**2.5s**
- 触发：`POST /generate`（播客或视频）后启动
- 停止条件：播客 `status` 不在 `generating|pausing`，且无任何 `video[*].status === generating`

```javascript
function taskNeedsPolling(task) {
  return task.status === "generating"
    || task.status === "pausing"
    || Object.values(task.video || {}).some(v => v.status === "generating");
}
```

---

## 7. `layout.json` 与渲染引擎的关系

UI **只负责编辑并持久化** `layout.json`；以下模块在 `generate_video()` 时读取它：

| 模块 | 使用的 region |
|------|---------------|
| `compose.py` | `page`：PDF 放置区域 |
| `key_points_render.py` | `title`, `key_points` |
| `page_images_render.py` | `figures` |

**成片图层顺序**（从底到顶）：

1. 白底 + PDF（page）
2. Figure overlay（figures）
3. `key_points.ass`（标题 + 板书）
4. `subtitles.ass`（字幕，最上层）

改 layout 后必须 **完整重跑** `POST /video/generate`；仅 remux 字幕不会更新布局。

---

## 8. 端到端用户流程

```
1. 上传 PDF（文件或 URL）
      POST /api/tasks 或 /api/tasks/from-url
2. 生成播客
      POST /api/tasks/{id}/generate
      轮询直到 audio_ready === true
3. （可选）查看关键信息、PDF、播放音频
4. 制作视频
      4a. 选 16:9 → 编辑布局 → PUT layout
      4b. POST /video/generate
      4c. 轮询直到 video["16:9"].ready
5. 预览 / 下载 MP4
      GET /video?video_format=16:9
```

**视频生成硬依赖**：`input.pdf`、`podcast.mp3`、`transcript.txt` 均存在。

---

## 9. SPA / 独立前端迁移

若新 UI 不在 `static/` 同源 served：

1. 在 `app.py` 增加 CORS（允许前端 dev server origin）
2. 前端 API base URL 可配置（如 `VITE_API_BASE=http://127.0.0.1:8000`）
3. `app.py` 的 `GET /` 可改为返回 SPA 的 `index.html`，或 reverse proxy 统一入口

**无需改** `video_generator/` 与 layout API 路径。

---

## 10. 错误处理约定

| 场景 | HTTP | UI 处理 |
|------|------|---------|
| 任务不存在 | 404 | 提示并刷新列表 |
| layout 缺 region / 非法 JSON | 400 | 展示 `detail`，勿覆盖本地草稿 |
| 播客未就绪就生成视频 | 400 | 禁用视频按钮或提示先完成播客 |
| 视频生成失败 | `video[fmt].status=failed` | 展示 `error`；可查 `video_16x9/video_run.log` |
| preview figure 不存在 | 404 | 显示占位「Figure 图表」 |

---

## 11. 验收清单

重做 UI 后，用已有任务（如 `c84f44155bb94afcbbd05fc3c849695f`）验证：

- [ ] 任务列表加载、切换任务
- [ ] PDF 上传与 URL 导入
- [ ] 播客生成、暂停、续传、播放（含生成中流式）
- [ ] `audio_ready` 后显示视频区块
- [ ] 16:9：GET layout 加载四区域 + 预览图/文
- [ ] 拖动/缩放后 PUT layout，`layout.json` 磁盘更新
- [ ] 恢复默认布局
- [ ] 9:16：不显示布局编辑器，可直接生成
- [ ] POST video/generate 后状态变为 generating → ready
- [ ] 视频预览与下载正常
- [ ] 修改 layout 后重新生成，成片区域位置变化
- [ ] 轮询在生成结束后自动停止

---

## 12. Codex 实施步骤（建议顺序）

1. **读参考**：`static/index.html` 中 `api()`、`layoutEditorState`、`startVideoGeneration`、`startPolling`
2. **搭骨架**：任务列表 + 详情路由（或单页双栏）
3. **接播客链路**：upload → generate → poll → audio player
4. **接视频链路**：format 切换 → generate → poll → video preview
5. **实现 LayoutEditor 组件**：只依赖 §5.4–5.6 的 API 与 §6.3 契约
6. **删除或保留旧 UI**：替换 `static/index.html` 或改 `app.py` 静态挂载
7. **跑验收清单** §11

---

## 13. 明确不要改动的部分（除非产品需求变更）

- `layout.json` 的 region 名称：`title | key_points | page | figures`
- 16:9 画布分辨率 1920×1080（与 `video_presets.py` 绑定）
- 视频生成入口：`POST /api/tasks/{id}/video/generate`
- pipeline 读取 `work_dir/layout.json` 的路径约定

---

## 14. 可选扩展（非本次必须）

- 布局模板（多份 preset 存本地或后端）
- 9:16 自定义布局（需同步改 pipeline + preset）
- WebSocket 推送替代轮询
- 撤销/重做、对齐辅助线、网格吸附

---

## 15. 相关文档

- Pipeline 与布局渲染细节：`video_generator/README.md`（「画面布局编辑器（16:9）」章节）
- 布局模型源码：`video_generator/video_layout.py`
- API 实现：`app.py` L557–811

---

*文档版本：2026-07-17，对应当前 `app.py` + `static/index.html` 实现。*
