from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PageSlot:
    page_index: int
    start: float
    end: float
    image_path: Path


def build_equal_page_timeline(
    page_paths: list[Path],
    audio_duration: float,
    *,
    first_page_weight: float = 1.0,
) -> list[PageSlot]:
    if not page_paths:
        raise ValueError("No PDF pages provided")
    if audio_duration <= 0:
        raise ValueError(f"Invalid audio duration: {audio_duration}")

    page_count = len(page_paths)
    if page_count == 1:
        return [
            PageSlot(
                page_index=0,
                start=0.0,
                end=audio_duration,
                image_path=page_paths[0],
            )
        ]

    weights = [first_page_weight] + [1.0] * (page_count - 1)
    total_weight = sum(weights)
    slots: list[PageSlot] = []
    cursor = 0.0
    for index, image_path in enumerate(page_paths):
        if index == page_count - 1:
            end = audio_duration
        else:
            end = cursor + audio_duration * weights[index] / total_weight
        slots.append(
            PageSlot(
                page_index=index,
                start=cursor,
                end=end,
                image_path=image_path,
            )
        )
        cursor = end
    return slots


def write_concat_demuxer(slots: list[PageSlot], path: Path) -> None:
    lines = ["ffconcat version 1.0"]
    for slot in slots:
        duration = max(slot.end - slot.start, 0.05)
        escaped = slot.image_path.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
        lines.append(f"duration {duration:.3f}")
    last = slots[-1].image_path.resolve().as_posix().replace("'", "'\\''")
    lines.append(f"file '{last}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
