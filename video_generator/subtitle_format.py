from __future__ import annotations

import re
import unicodedata

_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)?")
_BRACKET_TOKEN_RE = re.compile(r"\[[^\]]+\]")
_DECIMAL_RE = re.compile(r"\d+\.\d+")
_CLAUSE_SPLIT_RE = re.compile(r"(?<=[，、；])")
_MIN_LINE_CONTENT_CHARS = 10


def normalize_flow_text(text: str) -> str:
    """Merge TTS pause punctuation into flowing prose for subtitle display."""
    text = text.strip()
    if not text:
        return text

    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)

    placeholders: dict[str, str] = {}

    def _stash_decimal(match: re.Match[str]) -> str:
        key = f"__DEC{len(placeholders)}__"
        placeholders[key] = match.group(0)
        return key

    text = _DECIMAL_RE.sub(_stash_decimal, text)
    text = re.sub(r"。(?=\S)", "，", text)
    text = re.sub(r"\.(?=\s*\S)", "，", text)

    for key, value in placeholders.items():
        text = text.replace(key, value)

    text = re.sub(r"，{2,}", "，", text)
    return re.sub(r"\s+", " ", text).strip()


def _char_width_unit(ch: str, *, latin_scale: float = 0.55) -> float:
    if ch.isspace():
        return 0.25
    if _CJK_RE.match(ch):
        return 1.0
    if unicodedata.east_asian_width(ch) in {"W", "F"}:
        return 1.0
    return latin_scale


def _measure_text(text: str) -> float:
    if not text:
        return 0.0
    total = sum(_char_width_unit(ch) for ch in text)
    return total


def _content_char_count(text: str) -> int:
    count = 0
    for ch in text:
        if ch.isspace() or ch in "，,、；。.!?！？":
            continue
        count += 1
    return count


def _tokenize(text: str) -> list[tuple[str, float]]:
    tokens: list[tuple[str, float]] = []
    index = 0
    while index < len(text):
        bracket = _BRACKET_TOKEN_RE.match(text, index)
        if bracket:
            word = bracket.group(0)
            width = _measure_text(word)
            tokens.append((word, width))
            index = bracket.end()
            continue

        latin = _LATIN_WORD_RE.match(text, index)
        if latin:
            word = latin.group(0)
            width = sum(_char_width_unit(ch) for ch in word)
            tokens.append((word, width))
            index = latin.end()
            continue

        ch = text[index]
        if ch.isspace():
            index += 1
            continue

        tokens.append((ch, _char_width_unit(ch)))
        index += 1

    return tokens


def _line_width(tokens: list[tuple[str, float]]) -> float:
    if not tokens:
        return 0.0
    return sum(width for _, width in tokens) + max(len(tokens) - 1, 0) * 0.15


def _wrap_tokens(tokens: list[tuple[str, float]], max_width_units: float) -> list[str]:
    if not tokens:
        return []

    lines: list[list[tuple[str, float]]] = []
    current: list[tuple[str, float]] = []
    current_width = 0.0

    def flush() -> None:
        nonlocal current, current_width
        if current:
            lines.append(current)
            current = []
            current_width = 0.0

    for token, width in tokens:
        extra = width if not current else width + 0.15
        if current and current_width + extra > max_width_units:
            if token[0] == "是" and current[-1][0] == "不":
                extra_allow = 1.2
                if current_width + extra <= max_width_units + extra_allow:
                    current.append((token, width))
                    current_width = _line_width(current)
                    continue
            flush()
        current.append((token, width))
        current_width = _line_width(current)

    flush()

    lines = _merge_orphan_lines(lines, max_width_units)
    return ["".join(token for token, _ in line) for line in lines]


def _merge_orphan_lines(
    lines: list[list[tuple[str, float]]],
    max_width_units: float,
) -> list[list[tuple[str, float]]]:
    if len(lines) <= 1:
        return lines

    merged: list[list[tuple[str, float]]] = []
    for line in lines:
        if merged and _line_width(line) <= 2.5:
            candidate = merged[-1] + line
            if _line_width(candidate) <= max_width_units:
                merged[-1] = candidate
                continue
        merged.append(line)

    if len(merged) > 1 and _line_width(merged[0]) <= 4.0:
        candidate = merged[0] + merged[1]
        if _line_width(candidate) <= max_width_units:
            merged = [candidate, *merged[2:]]

    return merged


def _split_clauses(text: str) -> list[str]:
    parts = [part.strip() for part in _CLAUSE_SPLIT_RE.split(text) if part.strip()]
    return parts or [text]


def wrap_subtitle_text(
    text: str,
    *,
    max_width_units: float,
) -> str:
    """Wrap text to lines; prefer comma breaks and avoid orphan short lines."""
    normalized = normalize_flow_text(text)
    if not normalized:
        return ""

    if _measure_text(normalized) <= max_width_units:
        return normalized

    lines: list[str] = []
    current = ""

    for clause in _split_clauses(normalized):
        candidate = f"{current}{clause}" if current else clause
        if _measure_text(candidate) <= max_width_units:
            current = candidate
            continue

        if current:
            if _content_char_count(current) < _MIN_LINE_CONTENT_CHARS and clause:
                wrapped = _wrap_tokens(_tokenize(candidate), max_width_units)
                if wrapped:
                    lines.extend(wrapped[:-1])
                    current = wrapped[-1]
                    continue
            lines.append(current)
            current = ""

        if _measure_text(clause) <= max_width_units:
            current = clause
            continue

        wrapped_parts = _wrap_tokens(_tokenize(clause), max_width_units)
        if wrapped_parts:
            lines.extend(wrapped_parts[:-1])
            current = wrapped_parts[-1]

    if current:
        if lines and _content_char_count(current) < _MIN_LINE_CONTENT_CHARS:
            combined = lines.pop() + current
            wrapped = _wrap_tokens(_tokenize(combined), max_width_units)
            lines.extend(wrapped)
        else:
            lines.append(current)

    return "\n".join(lines)


def max_width_units_for_canvas(
    *,
    canvas_width: int,
    margin_l: int,
    margin_r: int,
    font_size: int,
) -> float:
    usable = max(canvas_width - margin_l - margin_r, font_size * 4)
    return usable / font_size


def format_subtitle_for_ass(
    text: str,
    *,
    canvas_width: int,
    margin_l: int,
    margin_r: int,
    font_size: int,
) -> str:
    wrapped = wrap_subtitle_text(
        text,
        max_width_units=max_width_units_for_canvas(
            canvas_width=canvas_width,
            margin_l=margin_l,
            margin_r=margin_r,
            font_size=font_size,
        ),
    )
    return (
        wrapped.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )
