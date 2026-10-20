import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from llm_wiki_bridge import EssayPodWikiBridge


class _FakeCompiler:
    def __init__(self):
        self.calls = []

    def ingest(self, filename, content, progress=None):
        self.calls.append((filename, content))
        if progress:
            progress("requesting_llm", "test model call", 38)
        return SimpleNamespace(
            source_page="wiki/sources/example-paper.md",
            created=["wiki/sources/example-paper.md"],
            updated=["wiki/index.md"],
            warnings=[],
            knowledge_package="wiki/records/example-paper.json",
            knowledge_nodes=8,
            knowledge_edges=10,
            knowledge_evidence=6,
        )


class _FakeLinter:
    def __init__(self):
        self.calls = 0

    def run(self):
        self.calls += 1


class _FakeResearch:
    def __init__(self):
        self.starts = 0

    def background_status(self):
        return {"active": False}

    def start_background_pending(self):
        self.starts += 1
        return {"active": True, "queued": 1}

    def pause_background(self, timeout=105.0):
        return True


class EssayPodWikiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.fake_app = SimpleNamespace(
            config=SimpleNamespace(request_timeout_seconds=0.1),
            compiler=_FakeCompiler(),
            linter=_FakeLinter(),
            research=_FakeResearch(),
            knowledge=SimpleNamespace(
                aggregate_graph=lambda: {
                    "schema_version": "1.0",
                    "packages": 1,
                    "nodes": [],
                    "edges": [],
                    "evidence": [],
                }
            ),
        )
        wiki_root = Path(__file__).resolve().parents[1] / "llm_wiki"
        self.bridge = EssayPodWikiBridge(
            wiki_root,
            app_factory=lambda: self.fake_app,
        )

    def test_enqueue_compiles_and_persists_status(self):
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            pdf_path = task_dir / "input.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\nminimal test")

            self.bridge.enqueue("task-1", pdf_path, "example.pdf")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status = self.bridge.job_status("task-1", task_dir)
                if status["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.01)

            self.assertEqual(status["status"], "completed")
            self.assertEqual(status["page"], "wiki/sources/example-paper.md")
            self.assertEqual(self.fake_app.compiler.calls, [("example.pdf", pdf_path.read_bytes())])
            self.assertEqual(self.fake_app.linter.calls, 1)
            self.assertEqual(self.fake_app.research.starts, 1)
            saved = json.loads((task_dir / "llm_wiki.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "completed")
            self.assertEqual(saved["stage"], "completed")
            self.assertEqual(saved["progress"], 100)
            self.assertEqual(
                saved["knowledge_package"], "wiki/records/example-paper.json"
            )
            self.assertEqual(saved["knowledge_nodes"], 8)

    def test_stale_running_job_is_reported_as_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory)
            (task_dir / "llm_wiki.json").write_text(
                json.dumps({"task_id": "task-stale", "status": "running"}),
                encoding="utf-8",
            )
            status = self.bridge.job_status("task-stale", task_dir)
            self.assertEqual(status["status"], "interrupted")
            self.assertTrue(status["recoverable"])

    def test_recover_interrupted_requeues_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            tasks_root = Path(directory)
            task_dir = tasks_root / "task-recover"
            task_dir.mkdir()
            pdf_path = task_dir / "input.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\nrecover me")
            (task_dir / "llm_wiki.json").write_text(
                json.dumps(
                    {
                        "task_id": "task-recover",
                        "filename": "recover.pdf",
                        "status": "running",
                        "queued_at": "2026-08-03T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )

            recovered = self.bridge.recover_interrupted(tasks_root)
            self.assertEqual(recovered, ["task-recover"])
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                status = self.bridge.job_status("task-recover", task_dir)
                if status["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.01)
            self.assertEqual(status["status"], "completed")
            self.assertTrue(status["recovered"])
            self.assertEqual(status["retry_count"], 1)

    def test_bridge_exposes_atomic_knowledge_graph(self):
        graph = self.bridge.knowledge_graph()
        self.assertEqual(graph["schema_version"], "1.0")
        self.assertEqual(graph["packages"], 1)

    def test_embedded_graph_keeps_standalone_assets_but_uses_essaypod_routes(self):
        html = self.bridge.graph_view_html()
        self.assertIn('window.LLM_WIKI_GRAPH_ENDPOINT="/api/wiki/graph"', html)
        self.assertIn(
            'window.LLM_WIKI_ATOMIC_GRAPH_ENDPOINT="/api/wiki/knowledge-graph"',
            html,
        )
        self.assertIn('window.LLM_WIKI_PAGE_BASE="/api/wiki/page-view"', html)
        self.assertIn("/api/wiki/static/graph.js", html)
        self.assertRegex(html, r"/api/wiki/static/graph\.js\?v=\d+")
        self.assertRegex(html, r"/api/wiki/static/graph\.css\?v=\d+")
        self.assertIn("EssayPod Knowledge Base", html)
        self.assertIn('id="pageOverlay"', html)
        self.assertIn('id="pageFrame"', html)

    def test_embedded_page_exposes_google_translation_route(self):
        html = self.bridge.page_view_html()
        self.assertIn(
            'window.LLM_WIKI_TRANSLATION_ENDPOINT="/api/wiki/page-translation"',
            html,
        )
        self.assertIn('data-language="en"', html)
        self.assertIn('data-language="zh"', html)


if __name__ == "__main__":
    unittest.main()
