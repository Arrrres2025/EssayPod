from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app


class KnowledgeGraphOptOutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.temporary.name)
        self.tasks_patch = patch.object(app, "TASKS_DIR", self.tasks_dir)
        self.tasks_patch.start()

    def tearDown(self) -> None:
        self.tasks_patch.stop()
        self.temporary.cleanup()

    def test_legacy_tasks_remain_opted_in(self) -> None:
        self.assertTrue(app.normalize_include_in_knowledge_graph(None))
        self.assertTrue(app.normalize_include_in_knowledge_graph("true"))
        self.assertFalse(app.normalize_include_in_knowledge_graph("false"))

    def test_file_import_opt_out_is_persisted_and_skips_wiki(self) -> None:
        with (
            patch.object(app, "start_translation"),
            patch.object(app, "start_brief_generation"),
            patch.object(app.wiki_bridge, "enqueue") as enqueue,
            patch.object(app, "public_task", side_effect=lambda data: data),
        ):
            task = app.create_task_with_pdf(
                pdf_bytes=b"%PDF-1.7\nmock",
                filename="quick-read.pdf",
                condense_level="mid",
                include_in_knowledge_graph=False,
            )

        saved = app.read_metadata(task["id"])
        self.assertFalse(saved["include_in_knowledge_graph"])
        enqueue.assert_not_called()

    def test_file_import_default_still_enqueues_wiki(self) -> None:
        with (
            patch.object(app, "start_translation"),
            patch.object(app, "start_brief_generation"),
            patch.object(app.wiki_bridge, "enqueue") as enqueue,
            patch.object(app, "public_task", side_effect=lambda data: data),
        ):
            task = app.create_task_with_pdf(
                pdf_bytes=b"%PDF-1.7\nmock",
                filename="graph-paper.pdf",
                condense_level="mid",
            )

        saved = app.read_metadata(task["id"])
        self.assertTrue(saved["include_in_knowledge_graph"])
        enqueue.assert_called_once()

    def test_url_import_persists_opt_out_before_download_starts(self) -> None:
        with (
            patch.object(app, "normalize_pdf_url", return_value="https://example.test/paper.pdf"),
            patch.object(app, "enqueue_url_import") as enqueue,
            patch.object(app, "public_task", side_effect=lambda data: data),
        ):
            task = asyncio.run(
                app.create_task_from_url(
                    pdf_url="https://example.test/paper",
                    condense_level="low",
                    include_in_knowledge_graph=False,
                )
            )

        saved = app.read_metadata(task["id"])
        self.assertFalse(saved["include_in_knowledge_graph"])
        enqueue.assert_called_once_with(task["id"], "https://example.test/paper")

    def test_wiki_retry_cannot_override_original_opt_out(self) -> None:
        task_id = "f" * 32
        directory = self.tasks_dir / task_id
        directory.mkdir()
        app.write_metadata(
            task_id,
            {
                "id": task_id,
                "filename": "quick-read.pdf",
                "include_in_knowledge_graph": False,
            },
        )

        with self.assertRaises(HTTPException) as raised:
            app.retry_task_wiki(task_id)

        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
