from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import app


class UrlImportTests(unittest.TestCase):
    def _metadata(self, task_id: str) -> dict:
        return {
            "id": task_id,
            "title": "paper",
            "filename": "paper.pdf",
            "source_url": "https://example.test/paper.pdf",
            "requested_url": "https://example.test/paper.pdf",
            "condense_level": "mid",
            "status": "downloading",
            "error": "",
            "import_stage": "queued",
            "import_message": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
        }

    def test_enqueue_returns_before_slow_download_and_finishes_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_id = "a" * 32
            task_directory = root / task_id
            task_directory.mkdir()
            (task_directory / "metadata.json").write_text(
                json.dumps(self._metadata(task_id)), encoding="utf-8"
            )

            def fake_download(url, output, *, on_stage):
                on_stage("downloading")
                time.sleep(0.15)
                output.write_bytes(b"%PDF-1.7\nmock")
                return url

            def fake_generation(current_task_id):
                data = app.read_metadata(current_task_id)
                data["status"] = "generating"
                app.write_metadata(current_task_id, data)
                return data

            with (
                patch.object(app, "TASKS_DIR", root),
                patch.object(app, "download_pdf_from_url", side_effect=fake_download),
                patch.object(app, "start_task_generation", side_effect=fake_generation),
            ):
                started = time.monotonic()
                self.assertTrue(
                    app.enqueue_url_import(task_id, "https://example.test/paper.pdf")
                )
                self.assertLess(time.monotonic() - started, 0.1)
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    data = app.read_metadata(task_id)
                    if data["status"] == "generating":
                        break
                    time.sleep(0.01)

            self.assertEqual(data["status"], "generating")
            self.assertEqual(data["import_stage"], "processing")
            self.assertTrue((task_directory / "input.pdf").is_file())
            self.assertFalse((task_directory / "input.pdf.download").exists())

    def test_failed_download_is_persisted_for_retry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_id = "b" * 32
            task_directory = root / task_id
            task_directory.mkdir()
            (task_directory / "metadata.json").write_text(
                json.dumps(self._metadata(task_id)), encoding="utf-8"
            )
            with (
                patch.object(app, "TASKS_DIR", root),
                patch.object(
                    app,
                    "download_pdf_from_url",
                    side_effect=TimeoutError("source timed out"),
                ),
            ):
                app.run_url_import(task_id, "https://example.test/paper.pdf")
                data = app.read_metadata(task_id)

            self.assertEqual(data["status"], "failed")
            self.assertEqual(data["import_stage"], "failed")
            self.assertIn("source timed out", data["error"])


if __name__ == "__main__":
    unittest.main()
