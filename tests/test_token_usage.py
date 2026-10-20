from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import token_usage


class TokenUsageTests(unittest.TestCase):
    def test_reads_openai_and_audio_usage_shapes(self) -> None:
        self.assertEqual(
            token_usage.usage_from_response(
                {"usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}}
            )["total_tokens"],
            14,
        )
        usage = token_usage.usage_from_response(
            {"usage": {"input_text_tokens": 8, "output_audio_tokens": 12, "total_tokens": 20}}
        )
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (8, 12))

    def test_records_and_summarizes_actual_and_estimated_usage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "usage.jsonl"
            with patch.dict(os.environ, {"ESSAYPOD_TOKEN_USAGE_PATH": str(ledger)}):
                token_usage.record_response_usage(
                    {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}},
                    feature="wiki_compile",
                    provider="ark",
                    model="doubao",
                )
                token_usage.record_response_usage(
                    {},
                    feature="paper_chat",
                    provider="ark",
                    model="doubao",
                    input_text="Explain this paper",
                    output_text="A short answer",
                )
                summary = token_usage.summarize_day()
            self.assertEqual(summary["calls"], 2)
            self.assertGreater(summary["total_tokens"], 15)
            self.assertEqual(summary["actual_tokens"], 15)
            self.assertGreater(summary["estimated_tokens"], 0)
            self.assertEqual(summary["by_provider"][0]["name"], "ark")


if __name__ == "__main__":
    unittest.main()
