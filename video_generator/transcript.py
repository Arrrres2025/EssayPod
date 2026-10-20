from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROUND_RE = re.compile(r"^\[round (-?\d+)\] (\S+): (.+)$")


@dataclass(frozen=True)
class TranscriptLine:
    round_id: int
    speaker: str
    text: str


def parse_transcript(path: Path) -> list[TranscriptLine]:
    lines: list[TranscriptLine] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        match = ROUND_RE.match(raw)
        if not match:
            continue
        lines.append(
            TranscriptLine(
                round_id=int(match.group(1)),
                speaker=match.group(2),
                text=match.group(3).strip(),
            )
        )
    if not lines:
        raise ValueError(f"No transcript lines found in {path}")
    return lines


def transcript_to_alignment_text(lines: list[TranscriptLine]) -> str:
    return "".join(line.text for line in lines)


def transcript_to_display_text(lines: list[TranscriptLine]) -> str:
    return "\n".join(line.text for line in lines)
