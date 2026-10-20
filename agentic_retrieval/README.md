# EssayPod Two-Round Research Swarm

This package implements a bounded, evidence-grounded research swarm over the
compiled LLM Wiki.

## Runtime architecture

1. The Director reads the graph catalog and creates a diverse portfolio of up
   to five seeds.
2. Five role-specialized Workers read those seeds concurrently: landscape,
   methods, evaluation, criticism, and novelty.
3. Each Worker writes a typed memory item to an append-only shared blackboard:
   evidence, impression, or rejected_path.
4. The Director waits for the complete round-one snapshot and separates the
   query into known, uncertain, and unknown parts.
5. Up to five second-round Workers inspect suggested neighbor nodes in parallel.
   They receive the same immutable round-one memory snapshot.
6. The final synthesis is generated from the source evidence ledger. Impressions
   may organize hypotheses and next actions, but cannot be presented as facts.

## Important invariants

- Workers in the same round do not see partially written peer memories.
- Every memory records agent, role, node, confidence, relation to the query,
  citations, and next-node suggestions.
- A missing fact is not automatically considered a research gap.
- Proposal claims distinguish evidence, inference, and hypothesis.
- Factual paragraphs must contain exact Wiki source-page links.
- The current MVP is bounded to two rounds and ten Worker tasks.

## Runtime API

- POST /api/agentic-retrieval/sessions
- GET /api/agentic-retrieval/sessions/{session_id}
- GET /api/agentic-retrieval/sessions/{session_id}/events (SSE)
- POST /api/agentic-retrieval/sessions/{session_id}/cancel

## Main files

- service.py: session lifecycle, SSE, graph loading, local RAG and provenance.
- swarm.py: Director, parallel Workers, blackboard and research synthesis.
- IMPLEMENTATION_PLAN.md: staged path from the MVP to a proposal system.

## Rollback

The immediately preceding single-Agent version is stored under
ui_backups/essaypod-swarm-mvp-20260803-pre-change/.
