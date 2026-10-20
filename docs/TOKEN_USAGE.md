# EssayPod Token 用量账本

## 目标

侧栏个人工作区菜单中的“今日 Token 用量”来自后端统一账本，而不是前端估算。账本按调用追加 JSONL 记录，应用重启后仍然保留。

默认文件：`usage/token_usage.jsonl`

可通过 `ESSAYPOD_TOKEN_USAGE_PATH` 指定其他位置。写入失败只会放弃本次统计，不会让已经成功的论文处理、问答或 Wiki 编译失败。

## 已接入的调用

| 功能标识 | 功能 | 提供商 |
| --- | --- | --- |
| `paper_condense` | 论文内容浓缩 | Ark |
| `reviewer_brief` | 审稿人式导读 | Ark |
| `podcast_generation` | 播客生成（文本与音频 Token） | 火山播客 API |
| `paper_chat_routing` | 论文问答的意图规划 | Ark |
| `paper_chat_rerank` | 论文证据重排 | Ark |
| `paper_chat_answer` | 论文问答正文 | Ark |
| `video_key_points` | 视频要点生成 | Ark |
| `social_copy` | 社交发布文案 | Ark |
| `wiki_compile` | LLM Wiki 编译 | Ark / DeepSeek / Ollama |
| `wiki_query` | LLM Wiki 查询回答 | Ark / DeepSeek / Ollama |
| `wiki_web_research` | Wiki 网页背景补全总结 | Ark / DeepSeek / Ollama |

本地 FastEmbed、Tavily 搜索、PDF 解析、OCR、Google 翻译等不返回 LLM Token，因此不计入该指标。

## 实际值与估算值

- API 返回 `usage` 时，记录提供商给出的实际输入、输出、缓存和总 Token。
- 流式 Ark 请求显式请求最终 usage 事件。
- 火山播客 API 的 `input_text_tokens` 和 `output_audio_tokens` 会汇总为一次生成调用。
- 如果兼容接口没有返回 usage，系统会根据输入和输出文本做保守估算，并在菜单标注“含估算”。
- “API 实际”只表示当天所有已记录调用均获得了 API usage，不代表货币费用；不同模型、缓存 Token 和音频 Token 的计价不同。

## 接口

`GET /api/token-usage/today`

返回当天输入、输出、总 Token、调用次数、实际/估算拆分，以及按功能和提供商聚合的明细。菜单打开时立即读取，保持打开时每 15 秒刷新。

统计只从本功能启用后开始。启用前的历史调用没有完整 API usage，不能可靠回填。
