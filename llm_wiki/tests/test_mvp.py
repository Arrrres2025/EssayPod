from __future__ import annotations

import http.client
import json
import os
import subprocess
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from llm_wiki.compiler import WikiCompiler
from llm_wiki.config import Config
from llm_wiki.lint import WikiLinter
from llm_wiki.knowledge import (
    KNOWLEDGE_NODE_TYPES,
    KnowledgeStore,
    normalize_knowledge_package,
)
from llm_wiki.llm import (
    HTTPJSONBackend,
    MockBackend,
    RetryingFallbackBackend,
    backend_from_config,
)
from llm_wiki.markdown_view import render_page
from llm_wiki.models import (
    PageProposal,
    SourceAnalysis,
    WebConceptAnalysis,
    WebFact,
)
from llm_wiki.query import WikiQuery
from llm_wiki.server import App, make_handler
from llm_wiki.store import WikiStore, frontmatter, parse_frontmatter
from llm_wiki.web_research import WebResearchService
from http.server import ThreadingHTTPServer


PAPER = b"""Graph-Aware Retrieval for Persistent Knowledge Wikis

Graph-aware retrieval combines lexical evidence with linked concept pages.
Persistent knowledge wikis compile source documents before query time.
Bidirectional links support multi-hop retrieval across related concepts.
Graph-aware retrieval preserves provenance and exposes contradictory claims.
"""


def config_for(root: Path) -> Config:
    return Config(
        root=root,
        provider="mock",
        model="mock",
        base_url="",
        api_key="",
        host="127.0.0.1",
        port=0,
        max_upload_bytes=1024 * 1024,
        max_source_chars=120_000,
    )


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = config_for(self.root)
        self.store = WikiStore(self.root)
        self.store.ensure()
        self.compiler = WikiCompiler(self.config, self.store, MockBackend())

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_large_core_context_becomes_bounded_identity_registry(self) -> None:
        concept = self.store.page_path("concept", "Retrieval-Augmented Generation")
        self.store.atomic_write(
            concept,
            frontmatter(
                {
                    "title": "Retrieval-Augmented Generation",
                    "type": "concept",
                    "aliases": ["RAG"],
                }
            )
            + "\n\n# Retrieval-Augmented Generation\n",
        )
        (self.store.wiki / "index.md").write_text(
            "# Index\n" + ("large summary text\n" * 200),
            encoding="utf-8",
        )

        context = self.store.read_core_context(max_chars=500)

        self.assertLessEqual(len(context), 500)
        self.assertIn("Existing wiki page registry", context)
        self.assertIn("Retrieval-Augmented Generation", context)
        self.assertIn("aliases: RAG", context)

    def test_ingest_is_incremental_and_raw_is_immutable(self) -> None:
        first = self.compiler.ingest("paper.txt", PAPER, "graph retrieval")

        raw = self.root / first.source_file
        source_page = self.root / first.source_page
        self.assertTrue(raw.exists())
        self.assertTrue(source_page.exists())
        self.assertRegex(source_page.name, r"-[0-9a-f]{10}\.md$")
        self.assertGreaterEqual(len(first.created), 2)
        self.assertTrue(first.knowledge_package)
        package_path = self.root / first.knowledge_package
        self.assertTrue(package_path.is_file())
        package = json.loads(package_path.read_text(encoding="utf-8"))
        self.assertGreater(first.knowledge_nodes, 0)
        self.assertGreater(first.knowledge_evidence, 0)
        self.assertEqual(package["schema_version"], "1.0")
        before = raw.read_bytes()

        duplicate = self.compiler.ingest("renamed-paper.txt", PAPER)
        self.assertEqual(duplicate.created, [])
        self.assertEqual(duplicate.updated, [])
        self.assertTrue(duplicate.warnings)
        self.assertEqual(raw.read_bytes(), before)
        self.assertEqual(len(self.store.read_manifest()), 1)

    def test_atomic_research_knowledge_normalizes_all_supported_types(self) -> None:
        evidence = [
            {
                "id": "ev-1",
                "page": 2,
                "section": "Method",
                "quote": "The experiment evaluates the proposed method.",
                "confidence": 0.98,
            }
        ]
        raw_nodes = []
        labels = {
            "Concept": "Retrieval-Augmented Generation",
            "Claim": "The method improves answer quality",
            "Method": "Proposed Method",
            "Dataset": "Test Dataset",
            "Experiment": "Main experiment",
            "Metric": "Exact Match",
            "Result": "Exact Match reaches 47.3",
            "Limitation": "English-only evaluation",
            "Hypothesis": "The method improves answer quality",
            "Definition": "Definition of RAG",
            "ResearchQuestion": "Does the method improve answer quality?",
        }
        ids = {}
        for index, node_type in enumerate(sorted(KNOWLEDGE_NODE_TYPES), start=1):
            node_id = f"node-{index}"
            ids[node_type] = node_id
            raw_nodes.append(
                {
                    "id": node_id,
                    "type": node_type,
                    "label": labels[node_type],
                    "evidence_ids": ["ev-1"],
                    "confidence": 0.9,
                }
            )
        raw_edges = [
            {"source": "paper", "relation": "investigates", "target": ids["ResearchQuestion"]},
            {"source": "paper", "relation": "proposes", "target": ids["Method"]},
            {"source": ids["Definition"], "relation": "defines", "target": ids["Concept"]},
            {"source": ids["Hypothesis"], "relation": "tested_by", "target": ids["Experiment"]},
            {"source": ids["Experiment"], "relation": "uses", "target": ids["Method"]},
            {"source": ids["Experiment"], "relation": "evaluated_on", "target": ids["Dataset"]},
            {"source": ids["Experiment"], "relation": "produces", "target": ids["Result"]},
            {"source": ids["Result"], "relation": "measured_by", "target": ids["Metric"]},
            {"source": ids["Result"], "relation": "supports", "target": ids["Claim"]},
            {"source": ids["Claim"], "relation": "answers", "target": ids["ResearchQuestion"]},
            {"source": ids["Limitation"], "relation": "limits", "target": ids["Claim"]},
        ]
        for edge in raw_edges:
            edge["evidence_ids"] = ["ev-1"]
        package = normalize_knowledge_package(
            "paper-a-123",
            "raw/sources/paper-a.pdf",
            "Paper A",
            {"nodes": raw_nodes, "edges": raw_edges, "evidence": evidence},
        )

        self.assertEqual(
            {node["type"] for node in package["nodes"]}, KNOWLEDGE_NODE_TYPES
        )
        self.assertEqual(len(package["edges"]), len(raw_edges))
        self.assertEqual(len(package["evidence"]), 1)
        self.assertTrue(package["validation"]["valid"])
        self.assertTrue(
            all(node["evidence_ids"] for node in package["nodes"])
        )
        self.assertIn(
            "concept::retrieval-augmented-generation",
            {node["id"] for node in package["nodes"]},
        )

    def test_atomic_graph_aggregates_paper_packages(self) -> None:
        result = self.compiler.ingest("paper.txt", PAPER)
        graph = KnowledgeStore(self.store).aggregate_graph()
        self.assertEqual(graph["packages"], 1)
        self.assertIn(
            "Paper", {node["type"] for node in graph["nodes"]}
        )
        self.assertGreater(len(graph["evidence"]), 0)
        self.assertEqual(
            graph["packages"],
            len(list((self.root / "wiki" / "records").glob("*.json"))),
        )
        self.assertEqual(result.knowledge_package.split("/")[-1], next(
            (self.root / "wiki" / "records").glob("*.json")
        ).name)

    def test_page_graph_reuses_cache_until_a_wiki_page_changes(self) -> None:
        result = self.compiler.ingest("paper.txt", PAPER)
        app = App(self.config)
        with patch.object(
            app, "_build_page_graph", wraps=app._build_page_graph
        ) as build:
            first = app.graph()
            second = app.graph()
            self.assertIs(first, second)
            self.assertEqual(build.call_count, 1)

            source_page = self.root / result.source_page
            source_page.write_text(
                source_page.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            app.graph()
            self.assertEqual(build.call_count, 2)

    def test_atomic_normalizer_accepts_common_model_field_aliases(self) -> None:
        package = normalize_knowledge_package(
            "paper-alias",
            "raw/sources/paper.pdf",
            "Paper",
            {
                "evidence": [{"id": "e1", "quote": "Supported statement."}],
                "nodes": [
                    {"id": "c1", "type": "Claim", "label": "Claim", "evidence": ["e1"]},
                    {"id": "q1", "type": "ResearchQuestion", "label": "Question", "evidence": "e1"},
                ],
                "edges": [
                    {"from": "c1", "type": "answers", "to": "q1", "evidence": "e1"}
                ],
            },
            "Supported statement.",
        )

        self.assertEqual(len(package["edges"]), 1)
        self.assertEqual(package["edges"][0]["relation"], "answers")
        self.assertTrue(all(node["evidence_ids"] for node in package["nodes"]))
        self.assertTrue(package["edges"][0]["evidence_ids"])

    def test_atomic_normalizer_accepts_singular_nested_evidence_id(self) -> None:
        package = normalize_knowledge_package(
            "paper-singular",
            "raw/sources/paper.pdf",
            "Paper",
            {
                "evidence": [{"id": "e1", "quote": "Supported statement."}],
                "nodes": [
                    {
                        "id": "c1",
                        "type": "Claim",
                        "label": "Claim",
                        "evidence_id": "e1",
                    },
                    {
                        "id": "q1",
                        "type": "ResearchQuestion",
                        "label": "Question",
                        "attributes": {"evidence_id": "e1"},
                    },
                ],
                "edges": [
                    {
                        "source": "c1",
                        "relation": "answers",
                        "target": "q1",
                        "evidence_id": "e1",
                    }
                ],
            },
            "Supported statement.",
        )

        self.assertTrue(all(node["evidence_ids"] for node in package["nodes"]))
        self.assertTrue(package["edges"][0]["evidence_ids"])

    def test_atomic_normalizer_maps_relation_aliases(self) -> None:
        package = normalize_knowledge_package(
            "paper-a",
            "raw/sources/paper-a.txt",
            "Paper A",
            {
                "evidence": [{"id": "e1", "quote": "direct evidence"}],
                "nodes": [
                    {
                        "id": "m1",
                        "type": "Method",
                        "label": "Parent method",
                        "evidence_id": "e1",
                    },
                    {
                        "id": "c1",
                        "type": "Concept",
                        "label": "Component",
                        "evidence_id": "e1",
                    },
                ],
                "edges": [
                    {
                        "source": "m1",
                        "relation": "has_component",
                        "target": "c1",
                        "evidence_id": "e1",
                    }
                ],
            },
            "direct evidence",
        )

        self.assertEqual(package["edges"][0]["relation"], "has_part")
        self.assertFalse(
            any(
                "unsupported relation" in warning
                for warning in package["validation"]["warnings"]
            )
        )

    def test_evidence_verification_ignores_pdf_line_end_hyphenation(self) -> None:
        package = normalize_knowledge_package(
            "paper-layout",
            "raw/sources/paper.pdf",
            "Paper",
            {
                "evidence": [
                    {"id": "e1", "quote": "The model provides evidence."}
                ],
                "nodes": [
                    {
                        "id": "c1",
                        "type": "Claim",
                        "label": "Layout-safe evidence",
                        "evidence_ids": ["e1"],
                    }
                ],
                "edges": [],
            },
            "The model pro-\nvides evidence.",
        )

        self.assertTrue(package["evidence"][0]["verified"])
        self.assertNotIn("quote was not found", " ".join(package["validation"]["warnings"]))

    def test_duplicate_old_source_backfills_only_missing_atomic_package(self) -> None:
        first = self.compiler.ingest("paper.txt", PAPER)
        source_page = self.root / first.source_page
        source_before = source_page.read_text(encoding="utf-8")
        package_path = self.root / first.knowledge_package
        package_path.unlink()

        backfilled = self.compiler.ingest("paper.txt", PAPER)

        self.assertEqual(backfilled.created, [first.knowledge_package])
        self.assertEqual(backfilled.updated, [])
        self.assertIn("backfilled", " ".join(backfilled.warnings))
        self.assertTrue(package_path.is_file())
        self.assertEqual(
            source_page.read_text(encoding="utf-8"), source_before
        )
        self.assertIn(
            "knowledge-backfill |",
            (self.root / "wiki" / "log.md").read_text(encoding="utf-8"),
        )

    def test_interrupted_ingest_resumes_from_existing_raw_source(self) -> None:
        class InterruptedBackend(MockBackend):
            def analyze(self, filename, text, wiki_context, focus=""):
                raise RuntimeError("temporary provider failure")

        interrupted = WikiCompiler(
            self.config, self.store, InterruptedBackend()
        )
        with self.assertRaisesRegex(RuntimeError, "temporary provider failure"):
            interrupted.ingest("paper.txt", PAPER)

        self.assertEqual(len(self.store.read_manifest()), 1)
        self.assertFalse(list((self.root / "wiki" / "sources").glob("*.md")))

        resumed = self.compiler.ingest("paper.txt", PAPER)
        self.assertTrue((self.root / resumed.source_page).is_file())
        self.assertIn("Resumed compilation", " ".join(resumed.warnings))
        self.assertEqual(len(self.store.read_manifest()), 1)

    def test_graph_query_save_and_lint(self) -> None:
        result = self.compiler.ingest("paper.txt", PAPER)
        source_slug = Path(result.source_page).stem
        concept_pages = sorted((self.root / "wiki" / "concepts").glob("*.md"))
        self.assertTrue(concept_pages)
        concept_text = concept_pages[0].read_text(encoding="utf-8")
        self.assertIn(f"[[{source_slug}]]", concept_text)
        self.assertIn("[[", concept_text)

        answer = WikiQuery(self.store, MockBackend()).ask(
            "How does graph retrieval preserve provenance?", save=True
        )
        self.assertTrue(answer["answer"])
        self.assertTrue(answer["pages"])
        self.assertTrue((self.root / answer["saved"]).exists())
        self.assertIn("Syntheses", (self.root / "wiki" / "index.md").read_text())

        lint = WikiLinter(self.store).run()
        self.assertTrue((self.root / lint["report"]).exists())
        self.assertIsInstance(lint["issues"], list)
        self.assertFalse(
            [
                issue
                for issue in lint["issues"]
                if issue["kind"] in {"broken-link", "pending-concept"}
            ]
        )
        log = (self.root / "wiki" / "log.md").read_text(encoding="utf-8")
        self.assertIn("ingest |", log)
        self.assertIn("query |", log)
        self.assertIn("lint |", log)

    def test_web_research_promotes_pending_concept(self) -> None:
        page = self.root / "wiki" / "concepts" / "adapter-training.md"
        self.store.atomic_write(
            page,
            "\n".join(
                [
                    frontmatter(
                        {
                            "title": "Adapter Training",
                            "type": "concept",
                            "created": "2026-07-30",
                            "updated": "2026-07-30",
                            "sources": ["raw/sources/test.txt"],
                            "tags": ["concept"],
                        }
                    ),
                    "",
                    "# Adapter Training",
                    "",
                    "A test concept.",
                    "",
                    "## Related Pages",
                    "",
                    "- [[lora]]",
                    "",
                    "## Sources",
                    "",
                    "- `raw/sources/test.txt`",
                ]
            ),
        )

        class FakeSearch:
            def search(self, concept: str) -> list[dict]:
                return [
                    {
                        "id": "S1",
                        "title": "LoRA documentation",
                        "url": "https://example.com/lora",
                        "domain": "example.com",
                        "score": 0.95,
                        "snippet": "LoRA background.",
                        "content": "LoRA is discussed in this test evidence.",
                    }
                ]

        class UpgradeBackend(MockBackend):
            def research_concept(self, concept, sources):
                return WebConceptAnalysis(
                    title="Low-Rank Adaptation (LoRA)",
                    aliases=["LoRA"],
                    summary="A provisional LoRA summary from Web evidence.",
                    facts=[
                        WebFact(
                            text="LoRA is described by the selected source.",
                            source_ids=["S1"],
                        )
                    ],
                    connections=[],
                    caveats=[],
                )

            def analyze(self, filename, text, wiki_context, focus=""):
                return SourceAnalysis(
                    title="A LoRA Research Paper",
                    summary="A source-backed LoRA paper.",
                    key_claims=["The paper provides LoRA evidence."],
                    key_quotes=[],
                    tags=["lora"],
                    pages=[
                        PageProposal(
                            kind="concept",
                            title="Low-Rank Adaptation (LoRA)",
                            summary="A source-backed LoRA summary.",
                            facts=["The paper adds formal LoRA evidence."],
                            connections=[],
                            contradictions=[],
                        )
                    ],
                    overview_summary="The Wiki now has source-backed LoRA evidence.",
                    themes=["LoRA"],
                    open_questions=[],
                    glossary=[],
                )

        backend = UpgradeBackend()
        service = WebResearchService(
            self.config,
            self.store,
            backend,
            search_client=FakeSearch(),
        )
        self.assertEqual(service.pending()[0]["canonical_id"], "lora")
        search = service.search("LoRA")
        result = service.compile(search["session_id"], [0])
        concept = self.root / result["page"]
        self.assertTrue(concept.exists())
        meta, body = parse_frontmatter(concept.read_text(encoding="utf-8"))
        self.assertEqual(meta["canonical_id"], "lora")
        self.assertEqual(meta["status"], "web_bootstrap")
        self.assertIn("Provisional web background", body)
        self.assertTrue(list((self.root / "raw" / "web" / "lora").glob("*.json")))
        self.assertFalse(service.pending())

        upgraded = WikiCompiler(
            self.config, self.store, backend
        ).ingest(
            "lora-paper.txt",
            b"A sufficiently detailed source about Low-Rank Adaptation.",
        )
        self.assertIn("wiki/concepts/lora.md", upgraded.updated)
        upgraded_meta, upgraded_body = parse_frontmatter(
            concept.read_text(encoding="utf-8")
        )
        self.assertEqual(upgraded_meta["status"], "source_backed")
        self.assertIn("Superseded Web Summaries", upgraded_body)
        self.assertIn("raw/web/lora/", upgraded_body)

    def test_alias_resolution_drives_lint_graph_and_rendered_links(self) -> None:
        target = self.root / "wiki" / "concepts" / "low-rank-adaptation-lora.md"
        self.store.atomic_write(
            target,
            "\n".join(
                [
                    frontmatter(
                        {
                            "title": "Low-Rank Adaptation (LoRA)",
                            "type": "concept",
                            "created": "2026-07-30",
                            "updated": "2026-07-30",
                            "sources": ["raw/sources/lora.pdf"],
                            "tags": ["concept"],
                            "canonical_id": "low-rank-adaptation-lora",
                            "aliases": ["LoRA"],
                        }
                    ),
                    "",
                    "# Low-Rank Adaptation (LoRA)",
                    "",
                    "A parameter-efficient adaptation method.",
                    "",
                    "## Related Pages",
                    "",
                    "- No resolved related pages yet.",
                    "",
                    "## Sources",
                    "",
                    "- `raw/sources/lora.pdf`",
                ]
            ),
        )
        referrer = self.root / "wiki" / "concepts" / "adapter-training.md"
        self.store.atomic_write(
            referrer,
            "\n".join(
                [
                    frontmatter(
                        {
                            "title": "Adapter Training",
                            "type": "concept",
                            "created": "2026-07-30",
                            "updated": "2026-07-30",
                            "sources": ["raw/sources/adapter.pdf"],
                            "tags": ["concept"],
                        }
                    ),
                    "",
                    "# Adapter Training",
                    "",
                    "Adapter training uses [[lora|LoRA]].",
                    "",
                    "## Related Pages",
                    "",
                    "- [[lora]]",
                    "",
                    "## Sources",
                    "",
                    "- `raw/sources/adapter.pdf`",
                ]
            ),
        )

        self.assertEqual(self.store.resolve_wikilink("lora"), target.resolve())
        lint = WikiLinter(self.store).run()
        self.assertFalse(
            [issue for issue in lint["issues"] if issue["kind"] == "pending-concept"]
        )
        rendered = render_page(self.store, referrer)
        self.assertIn('class="wikilink"', rendered["html"])
        self.assertIn(
            "/page?path=wiki%2Fconcepts%2Flow-rank-adaptation-lora.md",
            rendered["html"],
        )
        graph = App(self.config).graph()
        self.assertIn(
            ("adapter-training", "low-rank-adaptation-lora"),
            {(link["source"], link["target"]) for link in graph["links"]},
        )

    def test_background_research_uses_hyphen_free_query_and_top_three(self) -> None:
        page = self.root / "wiki" / "concepts" / "agent-skills.md"
        self.store.atomic_write(
            page,
            "\n".join(
                [
                    frontmatter(
                        {
                            "title": "Agent Skills",
                            "type": "concept",
                            "created": "2026-07-30",
                            "updated": "2026-07-30",
                            "sources": ["raw/sources/skills.pdf"],
                            "tags": ["concept"],
                        }
                    ),
                    "",
                    "# Agent Skills",
                    "",
                    "Agent skills can connect to [[skill-to-lora]].",
                    "",
                    "## Related Pages",
                    "",
                    "- [[skill-to-lora]]",
                    "",
                    "## Sources",
                    "",
                    "- `raw/sources/skills.pdf`",
                ]
            ),
        )

        class RankedSearch:
            def __init__(self):
                self.queries = []

            def search(self, concept):
                self.queries.append(concept)
                return [
                    {
                        "id": f"S{index}",
                        "title": f"Ranked source {index}",
                        "url": f"https://example.com/{index}",
                        "domain": "example.com",
                        "score": 1 - index / 10,
                        "snippet": f"Evidence {index}.",
                        "content": f"Source evidence {index} about Skill to LoRA.",
                    }
                    for index in range(1, 5)
                ]

        class ResearchBackend(MockBackend):
            def research_concept(self, concept, sources):
                return WebConceptAnalysis(
                    title="Skill-to-LoRA",
                    aliases=["S2L"],
                    summary="A Web-bootstrapped background page.",
                    facts=[
                        WebFact(
                            text="The selected sources describe Skill-to-LoRA.",
                            source_ids=[source["id"] for source in sources],
                        )
                    ],
                    connections=[],
                    caveats=[],
                )

        search_client = RankedSearch()
        service = WebResearchService(
            replace(self.config, tavily_api_key="test-key"),
            self.store,
            ResearchBackend(),
            search_client=search_client,
        )
        status = service.start_background_pending()
        self.assertTrue(status["active"])
        service._background_thread.join(timeout=5)
        final = service.background_status()
        self.assertEqual(search_client.queries, ["skill to lora"])
        self.assertEqual(final["completed"], 1)
        self.assertEqual(final["failed"], 0)
        created = self.root / "wiki" / "concepts" / "skill-to-lora.md"
        meta, _body = parse_frontmatter(created.read_text(encoding="utf-8"))
        self.assertEqual(len(meta["sources"]), 3)

    def test_background_research_yields_between_concepts_and_resumes(self) -> None:
        service = WebResearchService(
            replace(self.config, tavily_api_key="test-key"),
            self.store,
            MockBackend(),
        )
        concepts = [
            {"canonical_id": "first", "label": "First", "status": "unresolved"},
            {"canonical_id": "second", "label": "Second", "status": "unresolved"},
        ]
        started = threading.Event()
        release = threading.Event()
        searched = []

        service.pending = lambda persist=True: concepts

        def fake_search(concept, target_slug=None):
            searched.append(target_slug)
            if target_slug == "first":
                started.set()
                release.wait(timeout=2)
            return {
                "session_id": target_slug,
                "candidates": [{"id": "S1"}, {"id": "S2"}, {"id": "S3"}],
            }

        service.search = fake_search
        service.compile = lambda session_id, selected: {
            "page": f"wiki/concepts/{session_id}.md"
        }

        service.start_background_pending()
        self.assertTrue(started.wait(timeout=1))
        threading.Timer(0.05, release.set).start()
        self.assertTrue(service.pause_background(timeout=1))
        self.assertEqual(searched, ["first"])
        paused = service.background_status()
        self.assertFalse(paused["active"])
        self.assertEqual(paused["queued"], 1)

        service.start_background_pending()
        service._background_thread.join(timeout=2)
        self.assertEqual(searched, ["first", "second"])
        self.assertEqual(service.background_status()["completed"], 2)


class ArkProviderTest(unittest.TestCase):
    def test_config_auto_selects_ark_without_exposing_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".env").write_text(
                "\n".join(
                    [
                        "ARK_API_KEY=test-secret",
                        "ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config = Config.load(root)
        self.assertEqual(config.provider, "ark")
        self.assertEqual(config.model, "doubao-seed-2-0-pro-260215")
        self.assertEqual(
            config.base_url, "https://ark.cn-beijing.volces.com/api/v3"
        )
        self.assertEqual(config.api_key, "test-secret")
        self.assertIsInstance(backend_from_config(config), HTTPJSONBackend)

    def test_essaypod_module_loads_shared_provider_keys(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "EssayPod" / "llm_wiki"
            root.mkdir(parents=True)
            (base / ".env").write_text(
                "\n".join(
                    [
                        "ARK_API_KEY=shared-ark-key",
                        "DEEPSEEK_API_KEY=shared-deepseek-key",
                        "TAVILY_API_KEY=shared-tavily-key",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                config = Config.load(root)
        self.assertEqual(config.api_key, "shared-ark-key")
        self.assertEqual(config.deepseek_api_key, "shared-deepseek-key")
        self.assertEqual(config.deepseek_model, "deepseek-v4-pro")
        self.assertEqual(config.tavily_api_key, "shared-tavily-key")
        self.assertEqual(config.provider, "ark")
        backend = backend_from_config(config)
        self.assertIsInstance(backend, RetryingFallbackBackend)
        self.assertEqual(backend.primary_attempts, 2)

    def test_ark_retries_twice_then_falls_back_to_deepseek(self) -> None:
        class FailingArk(MockBackend):
            def __init__(self):
                self.calls = 0

            def analyze(self, filename, text, wiki_context, focus=""):
                self.calls += 1
                raise RuntimeError("Ark unavailable")

        class WorkingDeepSeek(MockBackend):
            def __init__(self):
                self.calls = 0

            def analyze(self, filename, text, wiki_context, focus=""):
                self.calls += 1
                return super().analyze(filename, text, wiki_context, focus)

        primary = FailingArk()
        fallback = WorkingDeepSeek()
        backend = RetryingFallbackBackend(
            primary,
            fallback,
            primary_attempts=2,
            retry_delay_seconds=0,
        )
        result = backend.analyze(
            "paper.txt", PAPER.decode("utf-8"), "", ""
        )
        self.assertTrue(result.title)
        self.assertEqual(primary.calls, 2)
        self.assertEqual(fallback.calls, 1)

    def test_deepseek_uses_official_chat_endpoint(self) -> None:
        config = Config(
            root=Path("/tmp"),
            provider="deepseek",
            model="deepseek-v4-pro",
            base_url="https://api.deepseek.com",
            api_key="test-secret",
            host="127.0.0.1",
            port=0,
            max_upload_bytes=1024,
            max_source_chars=1024,
        )
        backend = HTTPJSONBackend(config)
        self.assertEqual(
            backend._chat_url(),
            "https://api.deepseek.com/chat/completions",
        )

    def test_http_backend_enforces_absolute_request_deadline(self) -> None:
        config = Config(
            root=Path("/tmp"),
            provider="ark",
            model="test-model",
            base_url="https://example.invalid",
            api_key="test-secret",
            host="127.0.0.1",
            port=0,
            max_upload_bytes=1024,
            max_source_chars=1024,
            request_timeout_seconds=0.02,
        )
        backend = HTTPJSONBackend(config)

        with patch(
            "llm_wiki.llm.subprocess.run",
            side_effect=subprocess.TimeoutExpired("http_worker", 0.02),
        ):
            with self.assertRaisesRegex(RuntimeError, "absolute 0.02s deadline"):
                backend._request("https://example.invalid", {})

    def test_large_paper_is_compiled_in_bounded_page_and_knowledge_stages(self) -> None:
        class StagedBackend(HTTPJSONBackend):
            def __init__(self, config: Config):
                super().__init__(config)
                self.features: list[str] = []

            def _complete_json(self, prompt: str, feature: str = "wiki_compile"):
                self.features.append(feature)
                if feature == "wiki_compile_pages":
                    self.assert_prompt = prompt
                    return {
                        "source": {
                            "title": "Large Paper",
                            "summary": "A bounded staged compilation.",
                            "key_claims": [],
                            "key_quotes": [],
                            "tags": [],
                        },
                        "pages": [],
                        "overview": {"summary": "Summary", "themes": [], "open_questions": []},
                        "glossary": [],
                    }
                return {
                    "knowledge": {
                        "nodes": [{"id": "rq", "type": "ResearchQuestion", "label": "Question"}],
                        "edges": [],
                        "evidence": [],
                    }
                }

        config = Config(
            root=Path("/tmp"),
            provider="ark",
            model="test-model",
            base_url="https://example.invalid",
            api_key="test-secret",
            host="127.0.0.1",
            port=0,
            max_upload_bytes=1024,
            max_source_chars=120_000,
        )
        backend = StagedBackend(config)

        analysis = backend.analyze("large.pdf", "x" * 60_001, "")

        self.assertEqual(
            backend.features,
            ["wiki_compile_pages", "wiki_compile_knowledge"],
        )
        self.assertEqual(analysis.title, "Large Paper")
        self.assertEqual(analysis.knowledge["nodes"][0]["type"], "ResearchQuestion")

    def test_large_paper_retries_each_stage_without_repeating_completed_stage(self) -> None:
        class StageBackend(HTTPJSONBackend):
            def __init__(self, config: Config, fallback: bool = False):
                super().__init__(config)
                self.fallback = fallback
                self.calls: list[str] = []

            def _complete_json(self, prompt: str, feature: str = "wiki_compile"):
                self.calls.append(feature)
                if feature == "wiki_compile_pages":
                    return {
                        "source": {"title": "Staged Paper", "summary": "Summary"},
                        "pages": [],
                        "overview": {"summary": "Summary"},
                        "glossary": [],
                    }
                if feature == "wiki_compile_foundations" and not self.fallback:
                    raise RuntimeError("primary foundation timeout")
                node_type = "Concept" if feature == "wiki_compile_foundations" else "Claim"
                prefix = "f" if node_type == "Concept" else "a"
                return {
                    "knowledge": {
                        "nodes": [{"id": f"{prefix}-1", "type": node_type, "label": node_type}],
                        "edges": [],
                        "evidence": [],
                    }
                }

        config = Config(
            root=Path("/tmp"), provider="ark", model="test", base_url="https://x",
            api_key="key", host="127.0.0.1", port=0, max_upload_bytes=1024,
            max_source_chars=120_000,
        )
        primary = StageBackend(config)
        fallback = StageBackend(replace(config, provider="deepseek"), fallback=True)
        backend = RetryingFallbackBackend(
            primary, fallback, primary_attempts=2, retry_delay_seconds=0
        )

        analysis = backend.analyze("large.pdf", "x" * 60_001, "")

        self.assertEqual(primary.calls.count("wiki_compile_pages"), 1)
        self.assertEqual(primary.calls.count("wiki_compile_foundations"), 2)
        self.assertEqual(fallback.calls, ["wiki_compile_foundations"])
        self.assertEqual(primary.calls.count("wiki_compile_findings"), 1)
        self.assertEqual(
            {node["type"] for node in analysis.knowledge["nodes"]},
            {"Concept", "Claim"},
        )

    def test_ark_uses_chat_endpoint_and_json_mode(self) -> None:
        class CapturingBackend(HTTPJSONBackend):
            def __init__(self, config: Config):
                super().__init__(config)
                self.calls: list[tuple[str, dict]] = []

            def _request(self, url: str, payload: dict) -> dict:
                self.calls.append((url, payload))
                return {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "source": {
                                            "title": "Test Source",
                                            "summary": "Test summary.",
                                            "key_claims": [],
                                            "key_quotes": [],
                                            "tags": [],
                                        },
                                        "pages": [],
                                        "overview": {
                                            "summary": "Test summary.",
                                            "themes": [],
                                            "open_questions": [],
                                        },
                                        "glossary": [],
                                    }
                                )
                            }
                        }
                    ]
                }

        config = Config(
            root=Path("/tmp"),
            provider="ark",
            model="doubao-seed-2-0-pro-260215",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key="test-secret",
            host="127.0.0.1",
            port=0,
            max_upload_bytes=1024,
            max_source_chars=1024,
        )
        backend = CapturingBackend(config)
        analysis = backend.analyze("test.txt", "A sufficiently long test source.", "")
        self.assertEqual(analysis.title, "Test Source")
        url, payload = backend.calls[0]
        self.assertEqual(
            url,
            "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
        )
        self.assertEqual(payload["model"], "doubao-seed-2-0-pro-260215")
        self.assertEqual(payload["response_format"], {"type": "json_object"})


class WebAPITest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(App(config_for(self.root)))
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(
        self, method: str, path: str, body: bytes | None = None, headers=None
    ) -> tuple[int, dict]:
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5
        )
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, data

    def test_upload_and_browse_api(self) -> None:
        boundary = "----llm-wiki-test-boundary"
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="focus"\r\n\r\n'
            "retrieval\r\n"
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="paper.txt"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode() + PAPER + f"\r\n--{boundary}--\r\n".encode()
        status, result = self.request(
            "POST",
            "/api/ingest",
            body,
            {
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
        )
        self.assertEqual(status, 200)
        self.assertTrue(result["source_page"])

        status, pages = self.request("GET", "/api/pages")
        self.assertEqual(status, 200)
        self.assertGreater(len(pages["pages"]), 3)

        status, graph = self.request("GET", "/api/graph")
        self.assertEqual(status, 200)
        self.assertGreater(len(graph["nodes"]), 3)
        self.assertGreater(len(graph["links"]), 0)
        self.assertTrue(
            {"source", "concept"}.issubset(
                {node["kind"] for node in graph["nodes"]}
            )
        )

        status, atomic_graph = self.request("GET", "/api/knowledge/graph")
        self.assertEqual(status, 200)
        self.assertEqual(atomic_graph["schema_version"], "1.0")
        self.assertEqual(atomic_graph["packages"], 1)
        self.assertIn(
            "Claim", {node["type"] for node in atomic_graph["nodes"]}
        )
        self.assertGreater(len(atomic_graph["evidence"]), 0)

        status, pending = self.request("GET", "/api/pending")
        self.assertEqual(status, 200)
        self.assertFalse(pending["available"])
        self.assertEqual(pending["concepts"], [])

        status, page = self.request(
            "GET", f"/api/page?path={result['source_page']}"
        )
        self.assertEqual(status, 200)
        self.assertIn("# Graph-Aware Retrieval", page["content"])
        self.assertIn("<h1>", page["html"])
        self.assertIn('class="wikilink"', page["html"])

        status, error = self.request("GET", "/api/page?path=../README.md")
        self.assertEqual(status, 400)
        self.assertIn("error", error)

        status, missing = self.request(
            "GET", "/api/page?path=wiki/sources/does-not-exist.md"
        )
        self.assertEqual(status, 404)
        self.assertIn("error", missing)


if __name__ == "__main__":
    unittest.main()
