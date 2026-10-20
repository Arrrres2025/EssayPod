from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class DeepLTranslationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.tasks_dir = Path(self.temporary.name)
        self.task_id = "a" * 32
        directory = self.tasks_dir / self.task_id
        directory.mkdir(parents=True)
        (directory / "input.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
        (directory / "metadata.json").write_text(
            json.dumps(
                {
                    "id": self.task_id,
                    "filename": "paper.pdf",
                    "title": "Paper",
                    "status": "ready",
                }
            ),
            encoding="utf-8",
        )
        app.ACTIVE_DEEPL_TRANSLATION_JOBS.clear()
        self.tasks_patch = patch.object(app, "TASKS_DIR", self.tasks_dir)
        self.tasks_patch.start()

    def tearDown(self) -> None:
        app.ACTIVE_DEEPL_TRANSLATION_JOBS.clear()
        self.tasks_patch.stop()
        self.temporary.cleanup()

    def test_reading_translation_state_never_starts_or_creates_deepl_job(self) -> None:
        state = app.read_translation_state(self.task_id)

        self.assertEqual(state["deepl_pdf_status"], "idle")
        self.assertFalse(state["deepl_pdf_ready"])
        self.assertFalse(app.deepl_translation_dir(self.task_id).exists())

    def test_explicit_start_queues_once(self) -> None:
        with patch.object(app.threading, "Thread") as thread_class:
            first = app.start_deepl_pdf_translation(self.task_id)
            second = app.start_deepl_pdf_translation(self.task_id)

        self.assertEqual(first["status"], "queued")
        self.assertEqual(second["status"], "queued")
        self.assertTrue(second["active"])
        self.assertEqual(thread_class.call_count, 1)
        thread_class.return_value.start.assert_called_once_with()

    def test_existing_deepl_pdf_is_reused_without_starting_job(self) -> None:
        output = app.deepl_translation_dir(self.task_id) / "input-mono.pdf"
        output.parent.mkdir(parents=True)
        output.write_bytes(b"%PDF translated")

        with patch.object(app.threading, "Thread") as thread_class:
            state = app.start_deepl_pdf_translation(self.task_id)

        self.assertEqual(state["status"], "ready")
        self.assertTrue(state["ready"])
        thread_class.assert_not_called()


if __name__ == "__main__":
    unittest.main()
