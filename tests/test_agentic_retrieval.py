from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from agentic_retrieval import AgenticRetrievalService


class FakeBackend:
    def answer(self, question: str, context: str) -> str:
        if "promising_neighbors" in question:
            return json.dumps(
                {
                    "relevance": 0.91,
                    "role": "evidence",
                    "summary": "该页面直接回答了测试问题。",
                    "useful_claims": ["XRAG evaluates retrieval and generation components."],
                    "promising_neighbors": [],
                }
            )
        return "XRAG evaluates components. [XRAG Paper](/api/wiki/page-view?path=wiki/sources/xrag.md)"


class FakeStore:
    def __init__(self, root: Path):
        self.root = root

    def list_pages(self):
        return list((self.root / "wiki").rglob("*.md"))

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


class FakeApp:
    def __init__(self, root: Path):
        self.backend = FakeBackend()
        self.store = FakeStore(root)

    def graph(self):
        return {
            "nodes": [
                {
                    "id": "xrag",
                    "label": "XRAG Paper",
                    "kind": "source",
                    "summary": "A modular RAG evaluation framework.",
                    "path": "wiki/sources/xrag.md",
                    "status": "source_backed",
                }
            ],
            "links": [],
        }


class AgenticRetrievalTests(unittest.TestCase):
    def test_emits_trace_and_cited_answer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "wiki" / "sources" / "xrag.md"
            source.parent.mkdir(parents=True)
            source.write_text(
                "---\ntitle: XRAG Paper\ntype: source\n---\n"
                "# XRAG\n\nXRAG evaluates retrieval and generation components.",
                encoding="utf-8",
            )
            app = FakeApp(root)
            service = AgenticRetrievalService(root, lambda: app)
            started = service.start(
                "How does XRAG evaluate RAG components?", max_nodes=4, max_depth=2
            )

            for _ in range(100):
                session = service.get(started["session_id"])
                if session["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.01)

            self.assertEqual(session["status"], "completed")
            self.assertTrue(session["evidence"])
            self.assertTrue(session["shared_memory"])
            self.assertEqual(session["rounds"][0]["round"], 1)
            self.assertIn(
                "/api/wiki/page-view?path=wiki/sources/xrag.md", session["answer"]
            )
            event_types = [
                event["type"] for event in service.events(started["session_id"])
            ]
            self.assertEqual(event_types[0], "session_started")
            self.assertIn("catalog_scanned", event_types)
            self.assertIn("director_plan", event_types)
            self.assertIn("round_started", event_types)
            self.assertIn("worker_started", event_types)
            self.assertIn("worker_completed", event_types)
            self.assertIn("shared_memory_updated", event_types)
            self.assertIn("director_review", event_types)
            self.assertEqual(event_types[-1], "completed")


if __name__ == "__main__":
    unittest.main()
