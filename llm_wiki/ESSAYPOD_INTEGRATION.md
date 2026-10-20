# EssayPod 集成说明

这个目录既是 LLM Wiki → Markdown Wiki → Graph View 完整管线的独立模块，
也已经通过 EssayPod 根目录的 `llm_wiki_bridge.py` 内嵌到主应用。独立 Web UI
仍然保留；EssayPod 产品界面只暴露 `Knowledge Graph` 一级入口。

## 1. 已包含的完整链路

```text
上传 PDF / Markdown / TXT 等材料
  → extractors.py 提取文本
  → compiler.py 调用 LLM 生成结构化 SourceAnalysis
  → store.py 增量合并 Source / Entity / Concept / Synthesis 页面
  → Markdown 中的 [[wikilink]] 形成持久知识关系
  → server.py 的 /api/graph 派生节点与边
  → graph-src.js 使用 PixiJS 8 + d3-force 渲染交互式 Graph
```

目录中包含 Python 包、Web UI、Graph 源码与 bundle、schema、测试、命令行入口、
安装脚本、架构文档和前端构建工具。

## 2. 有意未复制的内容

- `.env` 和任何 API Key
- `.venv`、`node_modules`、`__pycache__` 等可重建依赖
- 原项目上传的论文原文
- 原项目已经生成的 Wiki 页面和运行日志

因此，这是代码功能的完整副本，但数据层从空库开始，不会把研究材料或密钥混入
EssayPod。`WikiStore.ensure()` 创建数据目录；首次摄取、查询或 Lint 会按需创建
相应的核心页面和记录。

## 3. 独立运行

```bash
cd /Users/qizhonghao/Desktop/CUHK/text2vadio/EssayPod/llm_wiki
./setup.sh
./run.sh
```

默认界面：

- Wiki 构建器：`http://127.0.0.1:8765/`
- Graph View：`http://127.0.0.1:8765/graph`

模块会复用 `text2vadio/.env` 中的 `ARK_API_KEY`、`DEEPSEEK_API_KEY` 和
`TAVILY_API_KEY`。Ark 每次操作默认尝试两次；两次均失败后自动改用 DeepSeek。
也可以在本目录创建不提交版本控制的 `.env` 覆盖共享配置。变量示例见
`.env.example`。

## 4. 当前 EssayPod 内嵌方式

### 上传触发

EssayPod 的本地上传与 URL 导入在保存 `tasks/<id>/input.pdf` 后，会立即调用
`EssayPodWikiBridge.enqueue()`。编译在线程中执行，不阻塞创建任务的 HTTP 响应，
并把状态原子写入 `tasks/<id>/llm_wiki.json`。

所有论文更新同一个 `llm_wiki/wiki/`，所以 Bridge 用进程内互斥锁串行执行写操作：

```text
EssayPod upload
  → enqueue(task_id, input.pdf)
  → 等待已有 Web Research 写操作结束
  → compiler.ingest()
  → linter.run()
  → 自动启动 Pending Concept Web Research
  → 写入 tasks/<id>/llm_wiki.json
```

同一任务处于 `queued/running` 时重复调用不会重复入队。编译器仍以原始材料内容
哈希做全局去重，因此同一 PDF 即使由另一个 EssayPod 任务再次上传，也不会重复
创建 Wiki 来源页。

### 主应用 API

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/tasks/{id}/wiki` | 单篇论文的 Wiki 任务状态 |
| POST | `/api/tasks/{id}/wiki/retry` | 对失败或中断的 Wiki 任务断点续编 |
| GET | `/api/wiki/status` | Wiki 状态与队列统计 |
| GET | `/api/wiki/graph` | 最新 Graph JSON |
| GET | `/api/wiki/knowledge-graph` | 原子研究知识图：typed nodes、typed edges、EvidenceSpans |
| GET | `/api/wiki/graph-view` | EssayPod 内嵌 Graph HTML |
| GET | `/api/wiki/page?path=...` | Markdown 页面渲染数据 |
| POST | `/api/wiki/page-translation` | 使用 Google 免费翻译生成并缓存中文详情页 |
| GET | `/api/wiki/page-view?path=...` | 内嵌详情页 HTML |
| GET | `/api/wiki/static/{asset}` | 白名单内 Graph/Page 静态资源 |

`web_ui/app/page.tsx` 的 `paper | graph` surface 控制右侧主区域。Graph 使用
iframe 载入同一 FastAPI 后端的 `/api/wiki/graph-view`，因此 Graph 原有布局和
交互保持不变。节点详情以 Graph 文档内部的第二层 iframe 覆盖显示，不再导航走
Pixi Graph iframe；返回时只关闭详情层，所以节点位置、缩放、选中邻域与已经结束
的力导向状态都会原样保留。详情页内的 Wiki 链接继续在该覆盖层中跳转。

详情页提供 `English / 中文` 快速切换。中文首次生成时复用论文翻译管线中的
`pdf2zh.GoogleTranslator`，不调用 Ark、DeepSeek 或其他 LLM，也不产生 LLM
Token 统计。翻译只处理可见文本，Wiki 目标、URL、代码块和 Markdown 结构保持
不变；结果按原页面内容哈希写入 `runtime/page_translations/`，页面更新后会自动
使用新的缓存键重新翻译，英文原文始终不被覆盖。

### 独立调试 UI

`./run.sh` 启动的 `http://127.0.0.1:8765/` 与 `/graph` 未被删除或重定向，
仍可独立执行上传、Query、Lint、Pending Concept 与 Web Research。两种入口共享
同一套 Python 实现和同一份 Markdown Wiki；正常使用时建议只让一个服务执行写操作。

## 5. Web 背景概念补全

Lint 将悬空 wikilink 聚合为 Pending Concept。用户可以通过 Tavily 搜索候选
来源，勾选 1–3 个网页，再由 Ark 创建 `web_bootstrap` 页面。不可变网页快照
保存于 `raw/web/`；正式论文到来后，同一 canonical ID 或 alias 会被增量升级为
`source_backed`，Web 事实和被替换摘要仍保留用于审计。

## 6. 当前同步语义

上传编译成功后，Markdown Wiki 已立即更新。`/api/graph` 每次请求都会从最新
页面重新派生 Graph，所以不会维护第二份容易过期的图数据库。EssayPod 内嵌
Graph 每 5 秒获取一次 Graph JSON，并用节点/边指纹判断是否需要重绘；没有变化时
不重启力导向布局。独立 Graph 仍保持手动加载行为。

同一次编译还会生成 `wiki/records/<source-page>.json`。其中保存 Concept、Claim、
Method、Dataset、Experiment、Metric、Result、Limitation、Hypothesis、Definition、
ResearchQuestion，以及带页码与原文短引的 EvidenceSpan。EssayPod 通过
`/api/wiki/knowledge-graph` 聚合这些记录；现有页面级 Knowledge Graph UI 暂不默认
铺开原子节点，Agentic Retrieval 可以直接使用这个细粒度接口。

完整目录、数据契约和算法说明见 `ARCHITECTURE.md`。
