from __future__ import annotations

import unittest

import app


class LegacyTaskCompatibilityTests(unittest.TestCase):
    def test_missing_or_invalid_condense_level_defaults_to_mid(self) -> None:
        self.assertEqual(app.normalize_condense_level(None), "mid")
        self.assertEqual(app.normalize_condense_level(""), "mid")
        self.assertEqual(app.normalize_condense_level("legacy"), "mid")

    def test_valid_condense_level_is_normalized(self) -> None:
        self.assertEqual(app.normalize_condense_level("LOW"), "low")
        self.assertEqual(app.normalize_condense_level("mid"), "mid")
        self.assertEqual(app.normalize_condense_level("high"), "high")


if __name__ == "__main__":
    unittest.main()
