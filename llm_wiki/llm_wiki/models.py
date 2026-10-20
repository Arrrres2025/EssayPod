from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PageProposal:
    kind: str
    title: str
    summary: str
    facts: list[str] = field(default_factory=list)
    connections: list[str] = field(default_factory=list)
    contradictions: list[str] = field(default_factory=list)


@dataclass
class SourceAnalysis:
    title: str
    summary: str
    key_claims: list[str]
    key_quotes: list[str]
    tags: list[str]
    pages: list[PageProposal]
    overview_summary: str
    themes: list[str]
    open_questions: list[str]
    glossary: list[dict[str, str]]
    knowledge: dict = field(default_factory=dict)


@dataclass
class IngestResult:
    source_file: str
    source_page: str
    created: list[str]
    updated: list[str]
    warnings: list[str]
    knowledge_package: str = ""
    knowledge_nodes: int = 0
    knowledge_edges: int = 0
    knowledge_evidence: int = 0


@dataclass
class WebFact:
    text: str
    source_ids: list[str] = field(default_factory=list)


@dataclass
class WebConceptAnalysis:
    title: str
    aliases: list[str]
    summary: str
    facts: list[WebFact]
    connections: list[str]
    caveats: list[str]
