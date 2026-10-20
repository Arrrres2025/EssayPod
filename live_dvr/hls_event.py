from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any


PLAYLIST_NAME = "stream.m3u8"
STATE_NAME = "state.json"
SEGMENT_PATTERN = re.compile(r"^segment_(\d{6})\.ts$")
EXTINF_PATTERN = re.compile(r"^#EXTINF:([0-9.]+)")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _remove_endlist(playlist: Path) -> None:
    if not playlist.is_file():
        return
    lines = [
        line
        for line in playlist.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip() != "#EXT-X-ENDLIST"
    ]
    playlist.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _existing_segment_count(directory: Path) -> int:
    indexes = [
        int(match.group(1))
        for path in directory.glob("segment_*.ts")
        if (match := SEGMENT_PATTERN.match(path.name))
    ]
    return max(indexes, default=-1) + 1


def _reset_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for path in directory.iterdir():
        if (
            path.name in {PLAYLIST_NAME, STATE_NAME, "ffmpeg.log"}
            or path.name.endswith(".tmp")
            or SEGMENT_PATTERN.match(path.name)
        ):
            if path.is_file():
                path.unlink()


class LiveDVRRecorder:
    """Mirror a growing MP3 byte stream into an HLS EVENT playlist.

    The original MP3 writer remains authoritative. This recorder is deliberately
    best-effort: an unavailable or failed FFmpeg process never interrupts podcast
    generation.
    """

    def __init__(
        self,
        directory: Path,
        *,
        resume: bool = False,
        segment_seconds: float = 4.0,
        ffmpeg_command: str = "ffmpeg",
    ) -> None:
        self.directory = directory
        self.playlist_path = directory / PLAYLIST_NAME
        self.state_path = directory / STATE_NAME
        self.log_path = directory / "ffmpeg.log"
        self.process: subprocess.Popen[bytes] | None = None
        self.log_handle = None
        self.error = ""
        self.closed = False
        self.segment_seconds = max(1.0, segment_seconds)

        if resume:
            directory.mkdir(parents=True, exist_ok=True)
            _remove_endlist(self.playlist_path)
        else:
            _reset_directory(directory)

        executable = shutil.which(ffmpeg_command)
        if not executable:
            self.error = f"FFmpeg executable not found: {ffmpeg_command}"
            self._write_state("unavailable")
            return

        start_number = _existing_segment_count(directory)
        flags = "temp_file+independent_segments"
        if resume and start_number:
            flags += "+append_list"
        command = [
            executable,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "mp3",
            "-i",
            "pipe:0",
            "-map",
            "0:a:0",
            "-vn",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-f",
            "hls",
            "-hls_time",
            f"{self.segment_seconds:.3f}",
            "-hls_list_size",
            "0",
            "-hls_playlist_type",
            "event",
            "-hls_segment_type",
            "mpegts",
            "-hls_flags",
            flags,
            "-start_number",
            str(start_number),
            "-hls_segment_filename",
            str(directory / "segment_%06d.ts"),
            str(self.playlist_path),
        ]
        try:
            self.log_handle = self.log_path.open("a" if resume else "w", encoding="utf-8")
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self.log_handle,
            )
            self._write_state("recording")
        except Exception as exc:
            self.error = str(exc)
            if self.log_handle:
                self.log_handle.close()
                self.log_handle = None
            self.process = None
            self._write_state("failed")

    @property
    def available(self) -> bool:
        return bool(self.process and self.process.stdin and self.process.poll() is None)

    def write(self, audio: bytes) -> None:
        if not audio or not self.available:
            return
        assert self.process is not None and self.process.stdin is not None
        try:
            self.process.stdin.write(audio)
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            self.error = str(exc)
            self._write_state("failed")

    def close(self, *, completed: bool) -> None:
        if self.closed:
            return
        self.closed = True
        process = self.process
        if process and process.stdin:
            try:
                process.stdin.close()
            except OSError:
                pass
        if process:
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if process.returncode not in {0, None} and not self.error:
                self.error = f"FFmpeg exited with code {process.returncode}"
        if self.log_handle:
            self.log_handle.close()
            self.log_handle = None
        self._write_state("completed" if completed and not self.error else "interrupted")

    def _write_state(self, status: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        _atomic_json(
            self.state_path,
            {
                "status": status,
                "error": self.error,
                "segment_seconds": self.segment_seconds,
                "updated_at": time.time(),
            },
        )


def read_live_dvr_state(
    directory: Path,
    *,
    task_status: str = "",
    audio_ready: bool = False,
) -> dict[str, Any]:
    playlist = directory / PLAYLIST_NAME
    durations: list[float] = []
    segment_names: list[str] = []
    ended = False
    target_duration = 0
    if playlist.is_file():
        lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines:
            match = EXTINF_PATTERN.match(line.strip())
            if match:
                durations.append(float(match.group(1)))
            elif line.startswith("#EXT-X-TARGETDURATION:"):
                try:
                    target_duration = int(line.split(":", 1)[1])
                except ValueError:
                    pass
            elif SEGMENT_PATTERN.match(line.strip()):
                segment_names.append(line.strip())
            elif line.strip() == "#EXT-X-ENDLIST":
                ended = True

    stored: dict[str, Any] = {}
    state_path = directory / STATE_NAME
    if state_path.is_file():
        try:
            stored = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}

    segment_count = min(len(durations), len(segment_names))
    total_duration = round(sum(durations[:segment_count]), 3)
    is_live = task_status in {"generating", "pausing"} and not audio_ready
    return {
        "ready": segment_count > 0,
        "live": is_live,
        "ended": ended or audio_ready,
        "duration": total_duration,
        "segment_count": segment_count,
        "target_duration": target_duration or (math.ceil(max(durations)) if durations else 4),
        "status": stored.get("status") or ("ready" if segment_count else "idle"),
        "error": stored.get("error") or "",
        "updated_at": playlist.stat().st_mtime if playlist.is_file() else stored.get("updated_at"),
    }
