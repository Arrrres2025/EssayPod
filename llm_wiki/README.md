# Karpathy LLM Wiki MVP

一个最小但完整可运行的 LLM Wiki 构建器。它实现的不是“把论文切块后做
RAG”，而是把每份新材料一次性编译进一个会长期累积的 Markdown Wiki：
新建来源页、合并实体与概念页、保留矛盾、建立双向链接、更新总览与术语表，
并让后续检索优先读取已经编译的 Wiki。

Karpathy 的原文特意没有规定唯一的目录名或页面模板，因此这里所说的“结构一致”
是指完整保留原文的三层架构、三种操作和两个特殊文件；具体页面分类是这个 MVP
为研究论文场景给出的、可继续演化的一种 schema。完整映射和每个文件的职责见
[ARCHITECTURE.md](ARCHITECTURE.md)。

## 5 分钟运行

要求 Python 3.11 或更高版本。

```bash
cd EssayPod/llm_wiki
./setup.sh
./run.sh
```

浏览器打开 <http://127.0.0.1:8765>。没有配置 API Key 时默认使用完全离线的
`mock` 模式，适合验证上传 → 编译 → Graph → 检索 → 回写的完整流程。它不具备
真实语义理解能力。

若 `.env` 中已有 `ARK_API_KEY`，程序会自动启用火山方舟在线推理，并默认选择
旗舰文本模型 `doubao-seed-2-0-pro-260215`：

```dotenv
ARK_API_KEY=your-ark-key
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
# 以下两项可省略；列出是为了显式固定行为
LLM_WIKI_PROVIDER=ark
LLM_WIKI_MODEL=doubao-seed-2-0-pro-260215
```

这是在线推理接口，按在线推理规则计费；它不是
`https://ark.cn-beijing.volces.com/api/coding/v3` 的 Coding Plan 网关。

在 EssayPod 目录结构中，本模块会先读取自己的 `.env`，再读取
EssayPod 工作区父目录 `.env` 中尚未设置的变量，因此可以复用其中的
`ARK_API_KEY` 与 `TAVILY_API_KEY`，不会复制或输出密钥。

若使用本地 Ollama：

```bash
cp .env.example .env
```

然后在 `.env` 中设置：

```dotenv
LLM_WIKI_PROVIDER=ollama
LLM_WIKI_MODEL=llama3.1:8b
LLM_WIKI_BASE_URL=http://127.0.0.1:11434
```

也可以使用支持 `/v1/chat/completions` 和 JSON 输出的其他 OpenAI-compatible 服务：

```dotenv
LLM_WIKI_PROVIDER=openai-compatible
LLM_WIKI_MODEL=your-model
LLM_WIKI_BASE_URL=https://api.openai.com
LLM_WIKI_API_KEY=your-key
```

## 使用方式

Web 页面提供六个动作：

1. 上传 PDF、Markdown、TXT、HTML、CSV、RST 或 JSON 材料，可附加本次关注点。
2. 浏览编译后生成或更新的 Wiki 页面。
3. 针对 Wiki 提问，并选择把有价值的回答存回 `wiki/syntheses/`。
4. 执行 lint，寻找断链、孤儿页、矛盾、缺失来源等结构问题。
5. 打开 <http://127.0.0.1:8765/graph>，直接以 PixiJS + d3-force 浏览当前
   Wiki 页面和 wikilink 形成的交互式 Graph。点击节点可进入渲染后的 Markdown
   详情页，详情页里的已有 wikilink 可以继续跳转。
6. 系统在启动、上传编译和 Lint 后自动排队 Pending Concept：搜索时把 Pending
   ID 中的连字符转换为空格，默认采用 Tavily 排名前三的网页，再由 Ark 创建带
   `web_bootstrap`、`provisional` 和网页快照溯源的临时 Concept。UI 仍保留
   手动搜索、来源检查和重试入口。

每次新来源编译还会在 `wiki/records/<source-page>.json` 生成细粒度研究知识包，
表示 Concept、Claim、Method、Dataset、Experiment、Metric、Result、Limitation、
Hypothesis、Definition、ResearchQuestion，以及带页码和原文短引的 EvidenceSpan。
页面图继续由 `/api/graph` 提供；原子知识图由 `/api/knowledge/graph` 提供。

同样的操作可由命令行完成：

```bash
.venv/bin/llm-wiki status
.venv/bin/llm-wiki ingest /absolute/path/to/paper.pdf --focus "重点关注检索算法"
.venv/bin/llm-wiki query "这些方法的主要差异是什么？" --save
.venv/bin/llm-wiki lint
```

## 数据安全约束

- 原始材料按 SHA-256 保存到 `raw/sources/`，已有文件永不修改或覆盖。
- 同一字节内容重复上传时不会再次编译。
- 来源页带摘要哈希，标题相同的两篇论文不会互相覆盖。
- 生成知识只写入 `wiki/`；运行状态不被混入知识层。
- 新证据与旧结论冲突时，冲突进入 `## Contradictions`，不会静默删除旧结论。
- Web Search 只负责发现来源；所选网页正文与 URL 以哈希快照写入 `raw/web/`。
- Web 临时页面被论文材料升级时保留旧事实、来源和被替换的 Web 摘要。

## 当前 MVP 边界

- PDF 只支持带文本层的文件；扫描件需要后续接入 OCR。
- 默认词法检索只用于从 Wiki 选候选页，还没有 BM25、向量或图算法。
- 当前单次上传调用一次 LLM 同时生成页面 proposal 与原子研究知识，之后由确定性
  代码完成 ID 归一化、关系校验、证据绑定、页面合并和持久化。后续可在不改变
  Knowledge Package 契约的情况下升级为分阶段抽取与独立审查调用。
- 没有自动做事实验证；source provenance 代表“材料中出现”，不代表事实必然正确。
- Web 来源可能包含错误、网页变更或恶意提示；系统将正文标为不可信证据并要求
  每条事实绑定来源。自动选择采用搜索排名前三，用户仍需审核生成页面，必要时可
  手动更换来源后重建。
- 内置 Graph 是复现 Logseq Pixi Graph View 核心技术与交互的独立预览，不是完整
  Logseq 客户端；Markdown wikilink 也可直接由 Obsidian 打开并展示 Graph View。

若修改 `llm_wiki/web/graph-src.js`，重新打包浏览器脚本：

```bash
cd tools/graph-preview
pnpm install
pnpm run build
```

EssayPod 集成模式默认允许最大 100 MiB 的 PDF 容器（可通过
`LLM_WIKI_MAX_UPLOAD_MB` 调整）。模型输入仍由
`LLM_WIKI_MAX_SOURCE_CHARS` 截断，因此图片较多的 PDF 不会按文件体积直接
转化为同等规模的模型上下文。

## 开发验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q llm_wiki
```

修改 Python 源码后重新执行 `./setup.sh`，将新版本安装进虚拟环境。
