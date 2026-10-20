from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .align import SubtitleSegment, write_srt, write_subtitle_json
from .transcript import TranscriptLine, parse_transcript

ROUND_START_RE = re.compile(r"^\[round start\] ([^:]+): (.*)$")
ROUND_END_RE = re.compile(r"^\[round end\] (\{.*\})$")


@dataclass(frozen=True)
class TimedRound:
    round_id: int | None
    speaker: str
    text: str
    start: float
    end: float


def _parse_round_end(line: str) -> dict | None:
    match = ROUND_END_RE.match(line.strip())
    if not match:
        return None
    try:
        payload = ast.literal_eval(match.group(1))
    except (SyntaxError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def parse_run_log_timestamps(run_log_path: Path) -> list[TimedRound]:
    if not run_log_path.is_file():
        raise FileNotFoundError(f"run.log not found: {run_log_path}")

    pending: dict | None = None
    rounds: list[TimedRound] = []
    round_index = 0

    for raw in run_log_path.read_text(encoding="utf-8").splitlines():
        start_match = ROUND_START_RE.match(raw.strip())
        if start_match:
            pending = {
                "speaker": start_match.group(1).strip(),
                "text": start_match.group(2).strip(),
            }
            continue

        end_payload = _parse_round_end(raw)
        if end_payload is None or pending is None:
            continue

        start_time = float(end_payload.get("start_time", 0.0))
        end_time = float(end_payload.get("end_time", start_time))
        if end_time < start_time:
            end_time = start_time + float(end_payload.get("audio_duration", 0.0))

        rounds.append(
            TimedRound(
                round_id=round_index,
                speaker=pending["speaker"],
                text=pending["text"],
                start=start_time,
                end=end_time,
            )
        )
        round_index += 1
        pending = None

    return rounds


def merge_transcript_with_timestamps(
    transcript_lines: list[TranscriptLine],
    timed_rounds: list[TimedRound],
) -> list[TimedRound]:
    if len(transcript_lines) == len(timed_rounds):
        merged: list[TimedRound] = []
        for line, timing in zip(transcript_lines, timed_rounds, strict=True):
            merged.append(
                TimedRound(
                    round_id=line.round_id,
                    speaker=line.speaker,
                    text=line.text,
                    start=timing.start,
                    end=timing.end,
                )
            )
        return merged
    return timed_rounds


def timed_rounds_to_segments(
    timed_rounds: list[TimedRound],
) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    index = 1
    for item in timed_rounds:
        if item.round_id == 9999:
            continue
        text = item.text.strip()
        if not text:
            continue
        if item.end <= item.start:
            continue
        segments.append(
            SubtitleSegment(
                index=index,
                start=item.start,
                end=item.end,
                text=text,
            )
        )
        index += 1
    if not segments:
        raise ValueError("No subtitle segments built from podcast timestamps")
    return segments


def load_timed_rounds(task_dir: Path) -> list[TimedRound]:
    timed_json = task_dir / "transcript_timed.json"
    if timed_json.is_file():
        payload = json.loads(timed_json.read_text(encoding="utf-8"))
        return [
            TimedRound(
                round_id=entry.get("round_id"),
                speaker=str(entry.get("speaker", "")),
                text=str(entry.get("text", "")),
                start=float(entry["start"]),
                end=float(entry["end"]),
            )
            for entry in payload
        ]

    run_log = task_dir / "run.log"
    timed_rounds = parse_run_log_timestamps(run_log)
    transcript_path = task_dir / "transcript.txt"
    if transcript_path.is_file():
        timed_rounds = merge_transcript_with_timestamps(parse_transcript(transcript_path), timed_rounds)
    return timed_rounds


def build_subtitles_from_podcast_timestamps(
    task_dir: Path,
    out_srt: Path,
    out_json: Path,
) -> list[SubtitleSegment]:
    timed_rounds = load_timed_rounds(task_dir)
    segments = timed_rounds_to_segments(timed_rounds)
    out_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(segments, out_srt)
    write_subtitle_json(segments, out_json)
    timed_out = task_dir / "transcript_timed.json"
    if not timed_out.is_file():
        timed_out.write_text(
            json.dumps([asdict(item) for item in timed_rounds], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    print(
        f"[subtitles] built {len(segments)} round-level segments from podcast timestamps -> {out_srt}",
        flush=True,
    )
    return segments
