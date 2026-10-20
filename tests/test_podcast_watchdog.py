from __future__ import annotations

import unittest

from podcast_tts import AudioFrameWatchdog


class AudioFrameWatchdogTests(unittest.TestCase):
    def test_fails_when_first_audio_never_arrives(self) -> None:
        now = [100.0]
        watchdog = AudioFrameWatchdog(30, 60, clock=lambda: now[0])

        now[0] = 129.9
        self.assertEqual(watchdog.timeout_message(), "")
        now[0] = 130.0
        self.assertIn("no first audio frame", watchdog.timeout_message())

    def test_switches_to_idle_timeout_after_audio_starts(self) -> None:
        now = [100.0]
        watchdog = AudioFrameWatchdog(30, 60, clock=lambda: now[0])

        now[0] = 120.0
        watchdog.mark_audio()
        now[0] = 179.9
        self.assertEqual(watchdog.timeout_message(), "")
        now[0] = 180.0
        self.assertIn("no new frame", watchdog.timeout_message())

    def test_new_audio_resets_idle_deadline(self) -> None:
        now = [100.0]
        watchdog = AudioFrameWatchdog(30, 60, clock=lambda: now[0])

        now[0] = 120.0
        watchdog.mark_audio()
        now[0] = 170.0
        watchdog.mark_audio()
        now[0] = 220.0
        self.assertEqual(watchdog.timeout_message(), "")

if __name__ == "__main__":
    unittest.main()
