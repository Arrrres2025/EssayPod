from __future__ import annotations

import re

from .align import SubtitleSegment

_CLAUSE_SPLIT_RE = re.compile(r"(?<=[，。])")
_COMMA_CHARS = frozenset("，,、；")
_PERIOD_CHARS = frozenset("。.!?！？")
_MIN_CLAUSE_CHARS = 10


def _content_char_count(text: str) -> int:
    count = 0
    for ch in text:
        if ch.isspace() or ch in _COMMA_CHARS or ch in _PERIOD_CHARS:
            continue
        count += 1
    return count


def merge_short_clauses(clauses: list[str], *, min_chars: int = _MIN_CLAUSE_CHARS) -> list[str]:
    """Merge adjacent clauses until each group has at least min_chars content characters."""
    if not clauses:
        return []

    merged: list[str] = []
    buffer = ""
    for clause in clauses:
        buffer += clause
        if _content_char_count(buffer) >= min_chars:
            merged.append(buffer)
            buffer = ""

    if buffer:
        if merged:
            merged[-1] += buffer
        else:
            merged.append(buffer)
    return merged


def split_into_clauses(text: str) -> list[str]:
    """Split round text into short clauses at comma / full-stop boundaries."""
    cleaned = text.strip()
    if not cleaned:
        return []

    parts = [part.strip() for part in _CLAUSE_SPLIT_RE.split(cleaned) if part.strip()]
    return parts or [cleaned]


def clause_weight(text: str) -> int:
    """
    Weight for proportional timing within a round.

    Each visible character counts as 1; each comma or full stop counts as 2.
    Example: 5 chars + one comma -> 5 + 2 = 7.
    """
    weight = 0
    for ch in text:
        if ch.isspace():
            continue
        if ch in _COMMA_CHARS or ch in _PERIOD_CHARS:
            weight += 2
        else:
            weight += 1
    return max(weight, 1)


def expand_segments_by_clauses(segments: list[SubtitleSegment]) -> list[SubtitleSegment]:
    """Split each round segment into clause-level segments with weighted sub-timestamps."""
    expanded: list[SubtitleSegment] = []
    index = 1

    for segment in segments:
        clauses = merge_short_clauses(split_into_clauses(segment.text))
        if not clauses:
            continue

        duration = segment.end - segment.start
        if duration <= 0:
            continue

        if len(clauses) == 1:
            expanded.append(
                SubtitleSegment(
                    index=index,
                    start=segment.start,
                    end=segment.end,
                    text=clauses[0],
                )
            )
            index += 1
            continue

        weights = [clause_weight(clause) for clause in clauses]
        total_weight = sum(weights)
        if total_weight <= 0:
            continue

        cursor = segment.start
        for clause_index, (clause, weight) in enumerate(zip(clauses, weights, strict=True)):
            if clause_index == len(clauses) - 1:
                clause_end = segment.end
            else:
                clause_end = cursor + duration * weight / total_weight

            if clause_end <= cursor:
                clause_end = min(cursor + 0.05, segment.end)

            expanded.append(
                SubtitleSegment(
                    index=index,
                    start=cursor,
                    end=clause_end,
                    text=clause,
                )
            )
            index += 1
            cursor = clause_end

    return expanded
