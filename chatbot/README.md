# EssayPod Paper RAG

This directory contains EssayPod's standalone paper question-answering system.
The repositories under `chatbot/kotaemon-main`, `paper-qa-main`, and
`ragflow-main` are references only. Runtime code here does not import from or
modify them.

## Modules

- `document/`: bilingual Markdown parsing, stable block IDs, heading tree and
  1024-token hierarchical chunks with 128-token overlap.
- `retrieval/`: FastEmbed adapter, local BM25 and dense/sparse hybrid ranking.
- `answering/`: Ark streaming answer generation, multimodal evidence packaging,
  language matching and validated citation construction.
- `storage/`: task-scoped persistent multi-turn conversations.
- `pipeline.py`: idempotent index orchestration and state files.

## Reused design ideas

The following Kotaemon scripts were studied and rewritten into independent,
smaller EssayPod modules:

| Kotaemon reference | EssayPod-owned implementation |
| --- | --- |
| `kotaemon/embeddings/fastembed.py` | `retrieval/embedding.py` |
| `kotaemon/indices/vectorindex.py` | `retrieval/index.py` |
| `ktem/index/file/pipelines.py` | `retrieval/index.py` + `pipeline.py` |
| `kotaemon/indices/qa/citation.py` | `answering/citations.py` |
| `kotaemon/indices/qa/citation_qa.py` | `answering/service.py` |
| `ktem/reasoning/simple.py` | `answering/service.py` |

No Kotaemon runtime dependency remains.

## Generated task artifacts

```text
tasks/<task-id>/chatbot/
├── state.json
├── document_structure.json
├── chunks.json
├── embeddings.npy
├── index_state.json
└── conversations/
```

The index is created automatically after the existing translation pipeline
finishes. It shares `translation/input.md`, `input.zh.md`,
`media_manifest.json`, and `assets/`; PDF parsing and YOLO are never repeated.

## Retrieval defaults

- Target chunk: 820 tokens
- Hard maximum: 1024 tokens
- Adjacent overlap: 128 tokens inside the same section
- Retrieval: 65% dense + 35% BM25, with heading-aware local reranking
- Context: top 4 chunks and at most 3 retrieved media assets
- Memory: latest 10 user/assistant turns

## Conversation routing

Each turn is routed before retrieval:

- `follow_up`: reuses the previous turn's stored chunk IDs and citations without
  running retrieval again.
- `paper_question`: uses the existing reviewer brief and heading tree to create
  three focused queries, searches them in parallel, deduplicates them with
  reciprocal-rank fusion, and reranks the merged evidence.
- `general_question`: answers directly without claiming paper support or
  fabricating paper citations.

Assistant messages persist their intent, generated queries, evidence chunk IDs,
and citations so later turns can continue the same subject reliably.

Optional model-based auxiliary passes can be enabled with:

```bash
CHATBOT_LLM_QUERY_REWRITE=1
CHATBOT_LLM_RERANK=1
```

They are disabled by default because the local contextual rewrite and hybrid
rank avoid delaying the first streamed token.
