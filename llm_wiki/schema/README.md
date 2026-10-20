# Schema layer

`../AGENTS.md` is the human-readable canonical operating schema. The JSON
contract in this directory constrains the structured result expected from an
LLM during ingest. Prompts are assembled by `llm_wiki/llm.py` from the same
concepts so model providers can be replaced without changing the knowledge
layout.

- `ingest-output.schema.json` constrains uploaded-source compilation.
- `paper-knowledge.schema.json` constrains the normalized per-paper atomic graph:
  typed nodes, typed relations, EvidenceSpans, confidence and validation output.
- `web-concept-output.schema.json` constrains provisional Concept compilation
  from human-selected, untrusted Web evidence; every fact requires source IDs.
