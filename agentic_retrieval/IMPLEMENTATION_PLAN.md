# Research Swarm Implementation Plan

## Current milestone: two-round MVP

The current implementation validates the core control loop:

- one Director;
- five heterogeneous Worker roles;
- two bounded parallel rounds;
- immutable per-round shared-memory snapshots;
- typed blackboard memory;
- graph-path visualization;
- known / uncertain / unknown gap map;
- evidence-constrained answers and proposal-shaped synthesis.

Sessions and blackboard memories are currently held in process memory. Wiki
pages remain the durable knowledge source.

## Phase 1: durable swarm memory

Persist sessions, assignments, events, memory items and evidence links in
SQLite. Add resume, replay, query-scoped negative memory, session comparison,
and export. Keep factual evidence immutable; version impressions and hypotheses.

Acceptance criteria:

- a session survives App restart;
- the UI can replay the exact graph path;
- every final statement traces to memory and evidence records.

## Phase 2: precise provenance

Upgrade Wiki compilation to store source chunks with PDF page number, section,
character offsets and content hashes. Cite source paragraphs rather than only
Wiki source pages. Add duplicate-claim and contradiction links.

Acceptance criteria:

- clicking a citation opens the relevant paper page or highlighted excerpt;
- changed source text invalidates dependent memories;
- unsupported generated claims fail a citation validator.

## Phase 3: adaptive multi-round control

Replace the fixed two-round budget with a state-machine Director. It chooses
whether to expand, verify, challenge, search the Web, request another paper, or
stop. Add information-gain scoring, per-role budgets, duplicate-work locks and
dynamic Worker counts.

Suggested stop conditions:

- high-value frontier exhausted;
- marginal information gain below threshold for two rounds;
- all proposal requirements have verified coverage;
- unresolved issues require unavailable evidence;
- token or wall-time budget reached.

## Phase 4: novelty and gap verification

Every candidate gap creates a challenge task that searches Wiki titles, graph
neighbors, source chunks and, when authorized, Web literature. Classify gaps as:

- verified absence within searched scope;
- weakly supported opportunity;
- disputed by prior work;
- unknown because evidence coverage is insufficient.

Never label a gap as novel solely because Workers did not encounter it.

## Phase 5: Proposal Studio

Create a structured proposal object rather than one Markdown answer:

- motivation and research question;
- literature map;
- verified and candidate gaps;
- method hypotheses;
- datasets and benchmarks;
- baselines;
- metrics and statistical tests;
- ablations;
- compute and data requirements;
- risks, ethics and failure criteria;
- citation graph.

The user can lock or edit any section and rerun only affected research tasks.

## Phase 6: evaluation

Build an internal benchmark with expert-marked sources, gaps, baselines and
evaluation choices. Measure citation precision, source coverage, duplicated
effort, contradiction discovery, gap false-positive rate, usefulness, cost,
latency and marginal information per round.

Compare the swarm against lexical retrieval, vector RAG, the previous
single-Agent graph browser, and a long-context no-graph baseline.
