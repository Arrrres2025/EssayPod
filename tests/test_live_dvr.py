from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module
from live_dvr import LiveDVRRecorder, read_live_dvr_state


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required for Live DVR tests")
class LiveDVRRecorderTests(unittest.TestCase):
    def make_mp3(self, directory: Path, name: str, duration: int = 11) -> bytes:
        path = directory / name
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=440:sample_rate=24000:duration={duration}",
                "-ac",
                "1",
                "-b:a",
                "64k",
                str(path),
            ],
            check=True,
        )
        return path.read_bytes()

    def feed(self, recorder: LiveDVRRecorder, audio: bytes) -> None:
        for offset in range(0, len(audio), 4096):
            recorder.write(audio[offset : offset + 4096])

    def test_creates_seekable_event_playlist(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recorder = LiveDVRRecorder(root / "dvr", segment_seconds=2)
            self.assertTrue(recorder.available, recorder.error)
            self.feed(recorder, self.make_mp3(root, "source.mp3"))
            recorder.close(completed=True)

            playlist = (root / "dvr" / "stream.m3u8").read_text(encoding="utf-8")
            state = read_live_dvr_state(root / "dvr", task_status="ready", audio_ready=True)
            self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", playlist)
            self.assertIn("#EXT-X-ENDLIST", playlist)
            self.assertGreaterEqual(state["segment_count"], 4)
            self.assertGreater(state["duration"], 9)
            self.assertTrue(state["ready"])
            self.assertTrue(state["ended"])

    def test_resume_appends_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dvr_dir = root / "dvr"
            first = LiveDVRRecorder(dvr_dir, segment_seconds=2)
            self.feed(first, self.make_mp3(root, "first.mp3", duration=6))
            first.close(completed=False)
            first_count = read_live_dvr_state(dvr_dir)["segment_count"]

            second = LiveDVRRecorder(dvr_dir, resume=True, segment_seconds=2)
            self.assertTrue(second.available, second.error)
            self.feed(second, self.make_mp3(root, "second.mp3", duration=6))
            second.close(completed=True)
            state = read_live_dvr_state(dvr_dir, task_status="ready", audio_ready=True)

            self.assertGreater(state["segment_count"], first_count)
            self.assertEqual(
                state["segment_count"],
                len(list(dvr_dir.glob("segment_*.ts"))),
            )

    def test_serves_playlist_status_and_segments(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tasks_dir = root / "tasks"
            task_id = "a" * 32
            task_directory = tasks_dir / task_id
            task_directory.mkdir(parents=True)
            (task_directory / "metadata.json").write_text(
                '{"id":"' + task_id + '","status":"generating"}',
                encoding="utf-8",
            )
            recorder = LiveDVRRecorder(task_directory / "live_dvr", segment_seconds=2)
            self.feed(recorder, self.make_mp3(root, "http-source.mp3", duration=6))
            recorder.close(completed=False)

            with patch.object(app_module, "TASKS_DIR", tasks_dir):
                client = TestClient(app_module.app)
                status = client.get(f"/api/tasks/{task_id}/live-dvr/status")
                playlist = client.get(f"/api/tasks/{task_id}/live-dvr/stream.m3u8")
                segment = client.get(f"/api/tasks/{task_id}/live-dvr/segment_000000.ts")

            self.assertEqual(status.status_code, 200)
            self.assertTrue(status.json()["ready"])
            self.assertTrue(status.json()["live"])
            self.assertEqual(playlist.status_code, 200)
            self.assertIn("#EXTM3U", playlist.text)
            self.assertEqual(playlist.headers["content-type"], "application/vnd.apple.mpegurl")
            self.assertEqual(segment.status_code, 200)
            self.assertEqual(segment.headers["content-type"], "video/mp2t")


if __name__ == "__main__":
    unittest.main()
