"""Export a PDF to structured Markdown, with optional block-level translation."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from string import Template
from typing import Iterable, Optional

import numpy as np
import pymupdf
from tqdm import tqdm

from pdf2zh.doclayout import OnnxModel
from pdf2zh.translator import (
    AnythingLLMTranslator,
    ArgosTranslator,
    AzureOpenAITranslator,
    AzureTranslator,
    BaseTranslator,
    BingTranslator,
    DeepLTranslator,
    DeepLXTranslator,
    DeepseekTranslator,
    DifyTranslator,
    GeminiTranslator,
    GoogleTranslator,
    GrokTranslator,
    GroqTranslator,
    MiniMaxTranslator,
    ModelScopeTranslator,
    OllamaTranslator,
    OpenAITranslator,
    OpenAIlikedTranslator,
    QwenMtTranslator,
    SiliconTranslator,
    TencentTranslator,
    X302AITranslator,
    XinferenceTranslator,
    ZhipuTranslator,
)

logger = logging.getLogger(__name__)

TEXT_TYPES = {
    "title",
    "plain text",
    "figure_caption",
    "table_caption",
    "table_footnote",
    "formula_caption",
}
MEDIA_TYPES = {"figure", "table", "isolate_formula"}
SKIP_TYPES = {"abandon"}
MERGEABLE_TYPE = "plain text"
# Block types whose `.text` is sent to the translator (structure shell stays local)
TRANSLATABLE_TYPES = {
    "title",
    "plain text",
    "figure_caption",
    "table_caption",
    "table_footnote",
    "formula_caption",
}

_TRANSLATOR_CLASSES = [
    GoogleTranslator,
    BingTranslator,
    DeepLTranslator,
    DeepLXTranslator,
    OllamaTranslator,
    XinferenceTranslator,
    AzureOpenAITranslator,
    OpenAITranslator,
    ZhipuTranslator,
    ModelScopeTranslator,
    SiliconTranslator,
    GeminiTranslator,
    AzureTranslator,
    TencentTranslator,
    DifyTranslator,
    AnythingLLMTranslator,
    ArgosTranslator,
    GrokTranslator,
    GroqTranslator,
    DeepseekTranslator,
    MiniMaxTranslator,
    OpenAIlikedTranslator,
    QwenMtTranslator,
    X302AITranslator,
]
NON_MERGE_BOUNDARY_TYPES = {
    "title",
    "figure",
    "figure_caption",
    "table",
    "table_caption",
    "table_footnote",
    "isolate_formula",
    "formula_caption",
}
SENTENCE_END = tuple(".?!。？！")
CONTINUATION_START = re.compile(
    r"^(and|or|but|the|a|an|of|in|on|for|to|with|as|by|from|that|which|who|"
    r"when|where|while|because|however|thus|therefore|also|then)\b",
    re.IGNORECASE,
)


@dataclass
class Block:
    type: str
    page: int
    bbox: tuple[float, float, float, float]  # x0, y0, x1, y1 (PyMuPDF / image top-left)
    text: str = ""
    asset_path: str = ""
    font_size: float = 0.0
    heading_level: int = 0  # 1/2/3 for titles after ranking
    conf: float = 0.0
    media_id: str = ""
    caption: str = ""
    # Side-by-side figures/tables share one id; reading order emits them atomically
    float_group: str = ""
    # Geometry used for column/full-width decisions (union of a float group)
    sort_bbox: tuple[float, float, float, float] | None = None
    # Stable order inside a float_group (media left→right, then captions)
    float_order: int = 0


def _normalize_type(name: str) -> str:
    return name.strip()


def _class_name(names, cls_id: int) -> str:
    if isinstance(names, dict):
        return _normalize_type(str(names[cls_id]))
    return _normalize_type(str(names[int(cls_id)]))


def _expand_bbox(
    bbox: tuple[float, float, float, float],
    page_rect: pymupdf.Rect,
    pad: float = 2.0,
    pad_x: float | None = None,
    pad_y: float | None = None,
) -> pymupdf.Rect:
    px = pad if pad_x is None else pad_x
    py = pad if pad_y is None else pad_y
    x0, y0, x1, y1 = bbox
    rect = pymupdf.Rect(x0 - px, y0 - py, x1 + px, y1 + py)
    return rect & page_rect


# Math / symbol fonts whose glyphs often leak into nearby text clips
_MATH_FONT_RE = re.compile(
    r"(CMMI|CMSY|CMEX|MSAM|MSBM|StMary|Euler|CambriaMath|RSA\w*Math|"
    r"MathItalic|MathSymbol|MathExtension|Symbol\b)",
    re.IGNORECASE,
)
_JUNK_LINE_RE = re.compile(
    r"^[_\W]*$"  # only punctuation/underscores
    r"|^[a-zA-Z]{1,3}(\s+[a-zA-Z]{1,3}){0,3}$"  # short letter crumbs: pp / g / f y
)


def _is_math_font(font: str) -> bool:
    return bool(_MATH_FONT_RE.search(font or ""))


# Common TeX → Unicode scripts (small CMMI/CMR spans are not true Unicode)
_SUBSCRIPT_MAP = str.maketrans(
    {
        "0": "₀",
        "1": "₁",
        "2": "₂",
        "3": "₃",
        "4": "₄",
        "5": "₅",
        "6": "₆",
        "7": "₇",
        "8": "₈",
        "9": "₉",
        "+": "₊",
        "-": "₋",
        "=": "₌",
        "(": "₍",
        ")": "₎",
        "a": "ₐ",
        "e": "ₑ",
        "h": "ₕ",
        "i": "ᵢ",
        "j": "ⱼ",
        "k": "ₖ",
        "l": "ₗ",
        "m": "ₘ",
        "n": "ₙ",
        "o": "ₒ",
        "p": "ₚ",
        "r": "ᵣ",
        "s": "ₛ",
        "t": "ₜ",
        "u": "ᵤ",
        "v": "ᵥ",
        "x": "ₓ",
    }
)
_SUPERSCRIPT_MAP = str.maketrans(
    {
        "0": "⁰",
        "1": "¹",
        "2": "²",
        "3": "³",
        "4": "⁴",
        "5": "⁵",
        "6": "⁶",
        "7": "⁷",
        "8": "⁸",
        "9": "⁹",
        "+": "⁺",
        "-": "⁻",
        "=": "⁼",
        "(": "⁽",
        ")": "⁾",
        "i": "ⁱ",
        "n": "ⁿ",
        "a": "ᵃ",
        "b": "ᵇ",
        "c": "ᶜ",
        "d": "ᵈ",
        "e": "ᵉ",
        "f": "ᶠ",
        "g": "ᵍ",
        "h": "ʰ",
        "j": "ʲ",
        "k": "ᵏ",
        "l": "ˡ",
        "m": "ᵐ",
        "o": "ᵒ",
        "p": "ᵖ",
        "r": "ʳ",
        "s": "ˢ",
        "t": "ᵗ",
        "u": "ᵘ",
        "v": "ᵛ",
        "w": "ʷ",
        "x": "ˣ",
        "y": "ʸ",
        "z": "ᶻ",
    }
)

# Accents that MuPDF often emits as a separate preceding glyph
_MATH_ACCENT_CHARS = set("ˆ^˜¯ˇ´`¨")

# CMEX often maps display ops / brace pieces poorly (PUA or letter stand-ins)
_CMEX_CHAR_MAP = str.maketrans(
    {
        "X": "∑",
        "S": "∑",
        "P": "∏",
    }
)


def _strip_private_use(text: str) -> str:
    """Drop Private Use Area glyphs (CMEX brace shards etc.)."""
    return "".join(ch for ch in text if not (0xE000 <= ord(ch) <= 0xF8FF))


def _to_script_text(text: str, kind: str) -> str:
    if kind == "super":
        return text.translate(_SUPERSCRIPT_MAP)
    return text.translate(_SUBSCRIPT_MAP)


def _compose_math_accents(text: str) -> str:
    """
    Merge split TeX accents: ``ˆy`` / ``^y`` → ``ŷ``.
    MuPDF often extracts the hat as its own CMR span before the base letter.
    """
    if not text:
        return text
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _MATH_ACCENT_CHARS and i + 1 < n:
            # skip spaces between accent and base
            j = i + 1
            while j < n and text[j] in " \t":
                j += 1
            if j < n and text[j].isalpha():
                base = text[j]
                if ch in {"ˆ", "^"}:
                    out.append(base + "\u0302")  # combining circumflex
                elif ch in {"˜"}:
                    out.append(base + "\u0303")
                elif ch in {"¯"}:
                    out.append(base + "\u0304")
                elif ch in {"¨"}:
                    out.append(base + "\u0308")
                elif ch in {"´"}:
                    out.append(base + "\u0301")
                elif ch in {"`"}:
                    out.append(base + "\u0300")
                elif ch in {"ˇ"}:
                    out.append(base + "\u030C")
                else:
                    out.append(ch)
                    out.append(base)
                i = j + 1
                continue
        out.append(ch)
        i += 1
    # NFC so ŷ prefers precomposed form when available
    return unicodedata.normalize("NFC", "".join(out))


def _clean_extracted_text(text: str) -> str:
    """Drop clip-fragment lines and trailing footnote markers."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    kept: list[str] = []
    for ln in lines:
        raw = ln.strip()
        if not raw:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if _JUNK_LINE_RE.match(raw):
            continue
        kept.append(ln.rstrip())
    # trim leading/trailing blank lines
    while kept and not kept[0].strip():
        kept.pop(0)
    while kept and not kept[-1].strip():
        kept.pop()
    text = "\n".join(kept)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Footnote markers stuck after sentence end: "...queries.i" / "...portals.g"
    # Require 2+ letters before punct so abbreviations like e.g. / i.e. survive.
    text = re.sub(r"(?<=[A-Za-z]{2})([.!?])([a-z])\b", r"\1", text)
    return text.strip()


def _extract_text_and_fontsize(
    page: pymupdf.Page, bbox: tuple[float, float, float, float]
) -> tuple[str, float]:
    """
    Extract text inside a layout box.

    Avoid vertical clip padding (it bisects neighboring glyphs and MuPDF then
    emits fragment letters like ``pp`` / ``g``).

    Inline math (CMMI / Greek / scripts) is **kept** as Unicode so
    ``where ŷᵢ = fθ(xᵢ)`` does not become ``where ˆ =.``. Plot-only crumbs
    (lone underscores) and non-math footnote markers are still dropped.
    Sub vs superscript uses span baseline (origin y) relative to the line.
    """
    # Horizontal pad only — vertical pad causes cross-line glyph fragments
    clip = _expand_bbox(bbox, page.rect, pad_x=1.0, pad_y=0.0)
    data = page.get_text("dict", clip=clip)

    # First pass: body font-size from non-math spans, ignoring script-sized CMR
    raw_body_sizes: list[float] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = span.get("text") or ""
                if not t.strip():
                    continue
                font = str(span.get("font") or "")
                size = float(span.get("size") or 0)
                if size <= 0 or _is_math_font(font):
                    continue
                raw_body_sizes.append(size)
    if raw_body_sizes:
        rough = float(np.median(raw_body_sizes))
        body_sizes = [s for s in raw_body_sizes if s >= rough * 0.9]
        median_size = float(np.median(body_sizes)) if body_sizes else rough
    else:
        median_size = 0.0
    # Footnote-sized non-math crumbs (but keep script-sized CMR next to math)
    min_size = median_size * 0.72 if median_size > 0 else 0.0

    out_lines: list[str] = []
    for block in data.get("blocks", []):
        for line in block.get("lines", []):
            spans = line.get("spans") or []
            line_has_math = any(
                _is_math_font(str(s.get("font") or ""))
                and (s.get("text") or "").strip()
                for s in spans
            )
            # Body baseline / size from larger non-script spans only
            raw_line_sizes = [
                float(s.get("size") or 0)
                for s in spans
                if (s.get("text") or "").strip() and float(s.get("size") or 0) > 0
            ]
            rough_line = max(raw_line_sizes) if raw_line_sizes else median_size
            body_origins: list[float] = []
            line_sizes: list[float] = []
            for s in spans:
                t = s.get("text") or ""
                # Whitespace-only spans often inherit a wrong baseline — skip
                if not t.strip():
                    continue
                size = float(s.get("size") or 0)
                if size <= 0 or size < rough_line * 0.9:
                    continue
                origin = s.get("origin") or (0.0, 0.0)
                body_origins.append(float(origin[1]))
                if not _is_math_font(str(s.get("font") or "")):
                    line_sizes.append(size)
            line_body = float(np.median(line_sizes)) if line_sizes else (
                float(rough_line) if rough_line else median_size
            )
            baseline_y = float(np.median(body_origins)) if body_origins else 0.0
            script_cut = line_body * 0.85 if line_body > 0 else 0.0
            # Clear sub/sup offset relative to body baseline (PDF y grows downward)
            script_dy = max(1.0, (line_body or median_size or 10.0) * 0.12)

            parts: list[str] = []
            for span in spans:
                t = span.get("text") or ""
                if not t:
                    continue
                font = str(span.get("font") or "")
                size = float(span.get("size") or 0)
                is_math = _is_math_font(font)
                is_cmex = "CMEX" in font.upper()
                origin = span.get("origin") or (0.0, 0.0)
                oy = float(origin[1])

                if is_cmex:
                    t = _strip_private_use(t.translate(_CMEX_CHAR_MAP))
                    if not t.strip():
                        continue

                is_script = bool(script_cut and size > 0 and size < script_cut)
                if is_script and baseline_y > 0:
                    if oy < baseline_y - script_dy:
                        t = _to_script_text(t, "super")
                    elif oy > baseline_y + script_dy * 0.35:
                        t = _to_script_text(t, "sub")
                    # else: small but on baseline — leave as-is (e.g. table marks)

                if is_math:
                    t = _strip_private_use(t)
                    if t:
                        parts.append(t)
                    continue

                # Script-sized CMR (=1 in ∑ᵢ₌₁) kept when line already has math
                if is_script and line_has_math:
                    parts.append(t)
                    continue

                # Drop non-math footnote markers / tiny ornaments only
                if min_size and size > 0 and size < min_size:
                    continue
                # Plot axis crumbs — do NOT drop ()[] which appear in f(x)
                if t.strip() in {"_", "|"}:
                    continue
                parts.append(t)

            line_text = _compose_math_accents("".join(parts))
            line_text = _strip_private_use(line_text).rstrip()
            if line_text.strip():
                out_lines.append(line_text)

    text = _clean_extracted_text("\n".join(out_lines))
    font_size = median_size
    return text, font_size


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax1 - ax0) * max(0.0, ay1 - ay0)
    area_b = max(0.0, bx1 - bx0) * max(0.0, by1 - by0)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _nms_blocks(blocks: list[Block], iou_thresh: float = 0.55) -> list[Block]:
    """Drop heavy overlaps; keep higher-confidence boxes."""
    kept: list[Block] = []
    for blk in sorted(blocks, key=lambda b: b.conf, reverse=True):
        if any(_iou(blk.bbox, k.bbox) >= iou_thresh and blk.type == k.type for k in kept):
            continue
        if any(
            _iou(blk.bbox, k.bbox) >= 0.85 and blk.type != k.type for k in kept
        ):
            # Nearly identical box with different label: keep the first (higher conf)
            continue
        kept.append(blk)
    return kept


def _geom(b: Block) -> tuple[float, float, float, float]:
    """BBox used for reading-order / full-width decisions."""
    return b.sort_bbox or b.bbox


def _bbox_center_x(b: Block) -> float:
    x0, _, x1, _ = _geom(b)
    return (x0 + x1) / 2.0


def _bbox_center_y(b: Block) -> float:
    _, y0, _, y1 = _geom(b)
    return (y0 + y1) / 2.0


def _bbox_union(
    boxes: Iterable[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    xs0, ys0, xs1, ys1 = zip(*boxes)
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def _y_overlap_ratio(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ay0, ay1 = a[1], a[3]
    by0, by1 = b[1], b[3]
    inter = min(ay1, by1) - max(ay0, by0)
    if inter <= 0:
        return 0.0
    return inter / max(1e-6, min(ay1 - ay0, by1 - by0))


def _is_full_width(b: Block, page_width: float) -> bool:
    """True for spans that cross the page gutter (titles, wide figures, etc.)."""
    if b.float_group:
        return True
    x0, _, x1, _ = _geom(b)
    width = x1 - x0
    if width >= page_width * 0.58:
        return True
    mid = page_width / 2.0
    gutter = page_width * 0.08
    return x0 < mid - gutter and x1 > mid + gutter


def _is_two_column_page(blocks: list[Block], page_width: float) -> bool:
    """Detect dual-column layout from narrow left/right block clusters."""
    mid = page_width / 2.0
    narrow = [b for b in blocks if not _is_full_width(b, page_width)]
    left = [b for b in narrow if _bbox_center_x(b) < mid]
    right = [b for b in narrow if _bbox_center_x(b) >= mid]
    if len(left) < 1 or len(right) < 1:
        return False
    # Require vertical overlap so a lone sidebar doesn't trigger dual-column mode
    ly0, ly1 = min(b.bbox[1] for b in left), max(b.bbox[3] for b in left)
    ry0, ry1 = min(b.bbox[1] for b in right), max(b.bbox[3] for b in right)
    overlap = min(ly1, ry1) - max(ly0, ry0)
    return overlap >= 40.0


def sort_blocks_reading_order(blocks: list[Block], page_width: float) -> list[Block]:
    """
    Reading order:
    - single column: top→bottom, then left→right
    - two column: full-width anchors by y; within each band, left column
      top→bottom, then right column top→bottom
    - float_group members (side-by-side figures + captions) emit atomically
    """
    if not blocks:
        return blocks

    by_group: dict[str, list[Block]] = {}
    for b in blocks:
        if b.float_group:
            by_group.setdefault(b.float_group, []).append(b)
    for members in by_group.values():
        members.sort(key=lambda b: (b.float_order, _geom(b)[0], _geom(b)[1]))

    def emit_one(b: Block, result: list[Block], emitted: set[int]) -> None:
        if id(b) in emitted:
            return
        if b.float_group and b.float_group in by_group:
            for m in by_group[b.float_group]:
                if id(m) not in emitted:
                    result.append(m)
                    emitted.add(id(m))
            return
        result.append(b)
        emitted.add(id(b))

    if not _is_two_column_page(blocks, page_width):
        ordered = sorted(blocks, key=lambda b: (_geom(b)[1], _geom(b)[0], b.float_order))
        result: list[Block] = []
        emitted: set[int] = set()
        for b in ordered:
            emit_one(b, result, emitted)
        return result

    mid = page_width / 2.0
    # One representative per float_group among full-width anchors
    full_reps: list[Block] = []
    seen_groups: set[str] = set()
    for b in blocks:
        if not _is_full_width(b, page_width):
            continue
        if b.float_group:
            if b.float_group in seen_groups:
                continue
            seen_groups.add(b.float_group)
        full_reps.append(b)
    full_reps.sort(key=lambda b: _geom(b)[1])

    cols = [b for b in blocks if not _is_full_width(b, page_width)]

    result = []
    emitted = set()
    prev_bottom = -1.0

    def emit_blocks(band: list[Block]) -> None:
        left = sorted(
            [b for b in band if _bbox_center_x(b) < mid],
            key=lambda b: (_geom(b)[1], _geom(b)[0]),
        )
        right = sorted(
            [b for b in band if _bbox_center_x(b) >= mid],
            key=lambda b: (_geom(b)[1], _geom(b)[0]),
        )
        for b in left + right:
            emit_one(b, result, emitted)

    def emit_column_band(y_top: float, y_bottom: float) -> None:
        in_band = [
            b
            for b in cols
            if id(b) not in emitted and y_top <= _bbox_center_y(b) < y_bottom
        ]
        emit_blocks(in_band)

    for full in full_reps:
        gy0, gy1 = _geom(full)[1], _geom(full)[3]
        emit_column_band(prev_bottom, gy0)
        overlapping = [
            b
            for b in cols
            if id(b) not in emitted and gy0 <= _bbox_center_y(b) < gy1
        ]
        emit_blocks(overlapping)
        emit_one(full, result, emitted)
        prev_bottom = gy1

    emit_column_band(prev_bottom, 1e9)

    missing = [b for b in blocks if id(b) not in emitted]
    if missing:
        emit_blocks([b for b in missing if not _is_full_width(b, page_width)])
        for b in sorted(
            [b for b in missing if _is_full_width(b, page_width)],
            key=lambda b: (_geom(b)[1], _geom(b)[0], b.float_order),
        ):
            emit_one(b, result, emitted)
    return result


# NOTE: do not use \b after ')' — ')' and following space are both non-word chars
_SUBFIG_CAPTION_RE = re.compile(
    r"^\s*\(([a-z]|[ivx]+)\)(?:\s|[.:：]|$)", re.IGNORECASE
)
_MAIN_CAPTION_RE = re.compile(
    r"^\s*(?:fig(?:ure)?\.?|table|formula|equation)\s*\d+", re.IGNORECASE
)


def _caption_kind(text: str) -> str:
    t = (text or "").strip()
    if _MAIN_CAPTION_RE.match(t):
        return "main"
    # Concatenated "(a) ... (b) ..." before single-sub check
    if re.search(r"\(a\)", t, re.I) and re.search(r"\(b\)", t, re.I):
        if not _MAIN_CAPTION_RE.match(t):
            return "concat"
    if _SUBFIG_CAPTION_RE.match(t):
        return "sub"
    return "other"


def _text_norm_for_cap(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _caption_is_redundant(cand: str, kept: list[str]) -> bool:
    """True if cand is a near-duplicate or concatenation of already-kept captions."""
    c = _text_norm_for_cap(cand)
    if not c:
        return True
    for k in kept:
        kn = _text_norm_for_cap(k)
        if not kn:
            continue
        if c == kn:
            return True
        if c in kn or kn in c:
            return True
    # Concat "(a)...(b)..." once any real caption is kept
    if _caption_kind(cand) == "concat" and kept:
        return True
    return False


def _cluster_side_by_side_media(
    media: list[Block], page_width: float
) -> list[list[Block]]:
    """Group horizontally adjacent same-type media with strong y-overlap."""
    if not media:
        return []
    remaining = sorted(media, key=lambda b: (b.bbox[1], b.bbox[0]))
    clusters: list[list[Block]] = []
    max_gap = page_width * 0.12

    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        changed = True
        while changed:
            changed = False
            still: list[Block] = []
            for b in remaining:
                if b.type != seed.type:
                    still.append(b)
                    continue
                linked = False
                for m in cluster:
                    yov = _y_overlap_ratio(m.bbox, b.bbox)
                    if yov < 0.4:
                        continue
                    # Horizontal gap (0 if boxes overlap in x)
                    if m.bbox[2] <= b.bbox[0]:
                        gap = b.bbox[0] - m.bbox[2]
                    elif b.bbox[2] <= m.bbox[0]:
                        gap = m.bbox[0] - b.bbox[2]
                    else:
                        gap = 0.0
                    # Side-by-side: close in x gap, distinct column centers
                    if gap <= max_gap and abs(
                        (m.bbox[0] + m.bbox[2]) / 2 - (b.bbox[0] + b.bbox[2]) / 2
                    ) >= page_width * 0.12:
                        linked = True
                        break
                if linked:
                    cluster.append(b)
                    changed = True
                else:
                    still.append(b)
            remaining = still
        clusters.append(sorted(cluster, key=lambda b: b.bbox[0]))
    return clusters


_MEDIA_NUM_RE = re.compile(
    r"^\s*(?P<kind>fig(?:ure)?\.?|table)\s*(?P<num>\d+)",
    re.IGNORECASE,
)


def _media_caption_num(text: str) -> tuple[str, str] | None:
    """Return ('figure'|'table', '4') for titles like ``Table 4: ...``."""
    m = _MEDIA_NUM_RE.match((text or "").strip())
    if not m:
        return None
    kind = m.group("kind").lower()
    kind = "figure" if kind.startswith("fig") else "table"
    return kind, m.group("num")


def _captions_for_media_cluster(
    cluster: list[Block],
    captions: list[Block],
    page_width: float,
) -> list[Block]:
    """
    Pick captions belonging to a media cluster.

    Important: do **not** use a tall proportional below-band — the next table's
    title (e.g. Table 5 under Table 4) sits only ~20pt below and would be
    swallowed, pulling right-column text into the float's y-span.
    """
    union = _bbox_union(m.bbox for m in cluster)
    ux0, uy0, ux1, uy1 = union
    media_kind = "table" if cluster[0].type == "table" else "figure"

    # Primary number from a main title sitting above the media
    primary_num: str | None = None
    for cap in captions:
        num = _media_caption_num(cap.text)
        if not num or num[0] != media_kind:
            continue
        cx0, cy0, cx1, cy1 = cap.bbox
        if cx1 < ux0 - page_width * 0.02 or cx0 > ux1 + page_width * 0.02:
            continue
        if cy1 <= uy0 + 12 and cy0 >= uy0 - 70:
            primary_num = num[1]
            break

    below_gap = 12.0  # tighter than typical gap to the *next* table title
    above_gap = 70.0
    picked: list[Block] = []
    for cap in captions:
        cx0, cy0, cx1, cy1 = cap.bbox
        if cx1 < ux0 - page_width * 0.02 or cx0 > ux1 + page_width * 0.02:
            continue

        kind = _caption_kind(cap.text)
        num = _media_caption_num(cap.text)
        above = (uy0 - above_gap) <= cy1 <= (uy0 + 14) and cy0 < uy0 + 8
        below = (uy1 - 6) <= cy0 <= (uy1 + below_gap)
        near_top_sub = kind == "sub" and (uy0 - 24) <= cy0 <= (uy0 + 10)

        if not (above or below or near_top_sub):
            continue

        # Reject "Table 5" when this cluster is already "Table 4"
        if num and num[0] == media_kind and primary_num and num[1] != primary_num:
            continue
        # Bare below main title with a different identity / too far → skip
        if num and below and cy0 > uy1 + below_gap:
            continue

        picked.append(cap)
        if primary_num is None and num and num[0] == media_kind and above:
            primary_num = num[1]
    return picked


def _dedupe_cluster_captions(captions: list[Block]) -> list[Block]:
    """Keep sub (a)/(b) + main Figure/Table N; drop concatenated duplicates."""
    if not captions:
        return []
    kinds = [(c, _caption_kind(c.text)) for c in captions]
    subs = [c for c, k in kinds if k == "sub"]
    mains = [c for c, k in kinds if k == "main"]
    concats = [c for c, k in kinds if k == "concat"]
    others = [c for c, k in kinds if k == "other"]

    ordered: list[Block] = []
    kept_texts: list[str] = []

    for c in sorted(subs, key=lambda b: (b.bbox[0], b.bbox[1])):
        if _caption_is_redundant(c.text, kept_texts):
            continue
        ordered.append(c)
        kept_texts.append(c.text)

    # Prefer the longest main caption (usually the real Figure N title)
    if mains:
        main = max(mains, key=lambda b: len(b.text.strip()))
        if not _caption_is_redundant(main.text, kept_texts):
            ordered.append(main)
            kept_texts.append(main.text)

    # Only keep concat if we have neither subs nor main
    if not ordered and concats:
        ordered.append(max(concats, key=lambda b: len(b.text.strip())))
        kept_texts.append(ordered[-1].text)

    for c in sorted(others, key=lambda b: (b.bbox[0], b.bbox[1])):
        if _caption_is_redundant(c.text, kept_texts):
            continue
        ordered.append(c)
        kept_texts.append(c.text)

    return ordered


def organize_side_by_side_floats(
    blocks: list[Block], page_width: float
) -> list[Block]:
    """
    Cluster side-by-side figures/tables, pair nearby captions, dedupe, and mark
    each cluster as one full-width float_group for atomic reading-order emit.
    """
    if not blocks:
        return blocks

    media = [b for b in blocks if b.type in {"figure", "table"}]
    captions = [
        b
        for b in blocks
        if b.type in {"figure_caption", "table_caption"}
    ]
    other = [
        b
        for b in blocks
        if b.type not in {"figure", "table", "figure_caption", "table_caption"}
    ]

    clusters = _cluster_side_by_side_media(media, page_width)
    claimed_caps: set[int] = set()
    claimed_media: set[int] = set()
    group_blocks: list[Block] = []
    gid = 0

    for cluster in clusters:
        # Solo media still may own multiple captions / need dedupe
        caps = _captions_for_media_cluster(cluster, captions, page_width)
        # Only form a float_group when side-by-side OR multiple captions
        if len(cluster) < 2 and len(caps) <= 1:
            continue

        expected_cap = "figure_caption" if cluster[0].type == "figure" else "table_caption"
        caps = [c for c in caps if c.type == expected_cap]
        # Claim all geometrically related captions so concat duplicates are not
        # re-emitted as free-floating leftovers after dedupe.
        for c in caps:
            claimed_caps.add(id(c))
        caps = _dedupe_cluster_captions(caps)
        if not caps and len(cluster) < 2:
            continue

        gid += 1
        group_id = f"p{cluster[0].page}-f{gid}"
        media_union = _bbox_union(m.bbox for m in cluster)
        # Media-only band cuts: including caption bboxes (esp. a wrong next-table
        # title) used to pull right-column crumbs like "tic workflows." into
        # the float overlap window.
        sort_bb = media_union

        uy0 = media_union[1]
        caps_above = sorted(
            [c for c in caps if c.bbox[3] <= uy0 + 14],
            key=lambda b: (b.bbox[1], b.bbox[0]),
        )
        caps_below = sorted(
            [c for c in caps if c not in caps_above],
            key=lambda b: (b.bbox[1], b.bbox[0]),
        )
        ordered_members = (
            caps_above + sorted(cluster, key=lambda b: b.bbox[0]) + caps_below
        )

        for order, m in enumerate(ordered_members):
            m.float_group = group_id
            m.sort_bbox = sort_bb
            m.float_order = order
            if m.type in {"figure", "table"}:
                claimed_media.add(id(m))
            group_blocks.append(m)

    leftover_media = [b for b in media if id(b) not in claimed_media]
    leftover_caps = [b for b in captions if id(b) not in claimed_caps]
    # Also pair leftover single media with its nearest non-claimed captions (dedupe)
    for m in leftover_media:
        near = _captions_for_media_cluster([m], leftover_caps, page_width)
        expected_cap = "figure_caption" if m.type == "figure" else "table_caption"
        near = _dedupe_cluster_captions([c for c in near if c.type == expected_cap])
        if len(near) <= 1:
            continue
        gid += 1
        group_id = f"p{m.page}-f{gid}"
        members = [m] + near
        sort_bb = _bbox_union(x.bbox for x in members)
        m.float_group = group_id
        m.sort_bbox = sort_bb
        m.float_order = 0
        claimed_media.add(id(m))
        group_blocks.append(m)
        for i, c in enumerate(near, start=1):
            c.float_group = group_id
            c.sort_bbox = sort_bb
            c.float_order = i
            claimed_caps.add(id(c))
            group_blocks.append(c)

    leftover_media = [b for b in media if id(b) not in claimed_media]
    leftover_caps = [b for b in captions if id(b) not in claimed_caps]
    return other + leftover_media + leftover_caps + group_blocks


def _join_plaintext(prev_text: str, nxt_text: str) -> str:
    prev_text = prev_text.rstrip()
    nxt_text = nxt_text.lstrip()
    if prev_text.endswith("-") and nxt_text[:1].islower():
        return prev_text[:-1] + nxt_text
    return re.sub(r" +", " ", (prev_text + " " + nxt_text).strip())


def _looks_like_column_continuation(prev_text: str, nxt_text: str) -> bool:
    """
    True when right-column text continues a left-column paragraph.

    Academic body almost always starts new paragraphs with a capital, so a
    lowercase right-column start after an unfinished left tail is a column wrap
    (e.g. ``... when a`` + ``verifier-facing artifact...``).
    """
    prev_text = prev_text.rstrip()
    nxt_text = nxt_text.lstrip()
    if not prev_text or not nxt_text:
        return False
    if nxt_text[:1].isupper():
        return False
    # Hyphenated wrap: agen- / tic
    if prev_text.endswith("-") and nxt_text[:1].islower():
        return True
    # Unfinished sentence / phrase on the left
    if prev_text.endswith(SENTENCE_END):
        return False
    # Lowercase / digit / open-paren continuation
    if nxt_text[:1].islower() or nxt_text[:1].isdigit() or nxt_text.startswith(("(", "[", "{")):
        return True
    return False


def merge_column_continued_text(
    blocks: list[Block], page_width: float
) -> list[Block]:
    """
    Join left-column paragraph tails with right-column continuations.

    Examples:
    - ``... real-time agen-`` + ``tic workflows.`` → ``agentic workflows.``
    - ``... and when a`` + ``verifier-facing artifact is complete...``
    """
    if not blocks:
        return blocks
    mid = page_width / 2.0
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        if (
            cur.type == MERGEABLE_TYPE
            and i + 1 < len(blocks)
            and blocks[i + 1].type == MERGEABLE_TYPE
        ):
            nxt = blocks[i + 1]
            cur_left = _bbox_center_x(cur) < mid
            nxt_right = _bbox_center_x(nxt) >= mid
            prev_text = cur.text.rstrip()
            nxt_text = nxt.text.lstrip()
            if (
                cur.page == nxt.page
                and cur_left
                and nxt_right
                and _looks_like_column_continuation(prev_text, nxt_text)
                and _font_sizes_close(cur.font_size, nxt.font_size)
            ):
                out.append(
                    Block(
                        type=cur.type,
                        page=cur.page,
                        bbox=cur.bbox,
                        text=_join_plaintext(prev_text, nxt_text),
                        font_size=cur.font_size or nxt.font_size,
                        conf=max(cur.conf, nxt.conf),
                    )
                )
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def merge_plaintext_split_by_floats(blocks: list[Block]) -> list[Block]:
    """
    Rejoin body text that YOLO/layout split around a mid-paragraph float.

    Pattern: plain_a + float_group... + plain_b  →  plain_ab + float_group...
    """
    if not blocks:
        return blocks

    out: list[Block] = []
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        if cur.type != MERGEABLE_TYPE:
            out.append(cur)
            i += 1
            continue

        # Collect following float_group (or lone media+captions)
        j = i + 1
        if j >= len(blocks):
            out.append(cur)
            i += 1
            continue

        float_run: list[Block] = []
        if blocks[j].float_group:
            gid = blocks[j].float_group
            while j < len(blocks) and blocks[j].float_group == gid:
                float_run.append(blocks[j])
                j += 1
        elif blocks[j].type in MEDIA_TYPES:
            cap_types = {
                "figure_caption",
                "table_caption",
                "formula_caption",
                "table_footnote",
            }
            while j < len(blocks) and (
                blocks[j].type in MEDIA_TYPES or blocks[j].type in cap_types
            ):
                float_run.append(blocks[j])
                j += 1
        else:
            out.append(cur)
            i += 1
            continue

        if not float_run or j >= len(blocks) or blocks[j].type != MERGEABLE_TYPE:
            out.append(cur)
            i += 1
            continue

        nxt = blocks[j]
        prev_text = cur.text.rstrip()
        nxt_text = nxt.text.lstrip()
        # Stitch when the break looks mid-sentence / mid-expression.
        # (Operator precedence: a bare "not ends with ." is too aggressive.)
        looks_broken = (
            prev_text.endswith(("(", "[", "{", "<", ">", "=", "−", "-", "≈", "|", ","))
            or nxt_text.startswith((")", "]", "}", ",", ";", ":", "|"))
            or bool(re.match(r"^[0-9]", nxt_text))  # "0.55)" continuing "0.45 <"
            or (
                not prev_text.endswith(SENTENCE_END)
                and (nxt_text[:1].islower() or bool(CONTINUATION_START.match(nxt_text)))
            )
        )
        # Cross-page OK: figure often sits on the next page mid-paragraph
        pages_ok = nxt.page == cur.page or nxt.page == cur.page + 1
        if looks_broken and pages_ok and _font_sizes_close(cur.font_size, nxt.font_size):
            out.append(
                Block(
                    type=cur.type,
                    page=cur.page,
                    bbox=cur.bbox,
                    text=_join_plaintext(prev_text, nxt_text),
                    font_size=cur.font_size or nxt.font_size,
                    conf=max(cur.conf, nxt.conf),
                )
            )
            out.extend(float_run)
            i = j + 1
            continue

        out.append(cur)
        i += 1
    return out


def detect_page_blocks(
    page: pymupdf.Page,
    page_index: int,
    model: OnnxModel,
    assets_dir: Path,
    media_counters: dict[str, int],
) -> list[Block]:
    pix = page.get_pixmap()
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)[
        :, :, ::-1
    ]
    imgsz = max(32, int(pix.height / 32) * 32)
    result = model.predict(image, imgsz=imgsz)[0]

    raw: list[Block] = []
    h, w = pix.height, pix.width
    for box in result.boxes:
        cls_id = int(box.cls)
        typ = _class_name(result.names, cls_id)
        if typ in SKIP_TYPES:
            continue
        x0, y0, x1, y1 = [float(v) for v in box.xyxy.squeeze()]
        x0 = float(np.clip(x0, 0, w))
        y0 = float(np.clip(y0, 0, h))
        x1 = float(np.clip(x1, 0, w))
        y1 = float(np.clip(y1, 0, h))
        if x1 <= x0 or y1 <= y0:
            continue
        # Scale pixmap pixels -> page points if DPI != 72
        sx = page.rect.width / w
        sy = page.rect.height / h
        bbox = (x0 * sx, y0 * sy, x1 * sx, y1 * sy)
        raw.append(
            Block(
                type=typ,
                page=page_index,
                bbox=bbox,
                conf=float(box.conf),
            )
        )

    blocks = _nms_blocks(raw)

    # Caption-driven local retry: layout models occasionally spot a caption but
    # miss its vector/composite media region at the normal render scale.  Retry
    # only those suspicious pages at 2x; this avoids the old remote-OCR delay.
    expected_media = {
        "figure_caption": "figure",
        "table_caption": "table",
        "formula_caption": "isolate_formula",
    }
    missing_types = {
        expected_media[b.type]
        for b in blocks
        if b.type in expected_media and not any(m.type == expected_media[b.type] for m in blocks)
    }
    if missing_types:
        retry_pix = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
        retry_image = np.frombuffer(retry_pix.samples, np.uint8).reshape(
            retry_pix.height, retry_pix.width, 3
        )[:, :, ::-1]
        retry_size = max(32, int(retry_pix.height / 32) * 32)
        retry_result = model.predict(retry_image, imgsz=retry_size)[0]
        retry_blocks: list[Block] = []
        rsx = page.rect.width / retry_pix.width
        rsy = page.rect.height / retry_pix.height
        for box in retry_result.boxes:
            typ = _class_name(retry_result.names, int(box.cls))
            if typ not in missing_types:
                continue
            x0, y0, x1, y1 = [float(v) for v in box.xyxy.squeeze()]
            retry_blocks.append(Block(
                type=typ,
                page=page_index,
                bbox=(x0 * rsx, y0 * rsy, x1 * rsx, y1 * rsy),
                conf=float(box.conf),
            ))
        blocks = _nms_blocks(blocks + retry_blocks)

    for blk in blocks:
        if blk.type in TEXT_TYPES:
            text, font_size = _extract_text_and_fontsize(page, blk.bbox)
            blk.text = text
            blk.font_size = font_size
        elif blk.type in MEDIA_TYPES:
            media_counters[blk.type] = media_counters.get(blk.type, 0) + 1
            idx = media_counters[blk.type]
            stem = blk.type.replace(" ", "_")
            filename = f"p{page_index + 1:02d}_{stem}_{idx:02d}.png"
            out_path = assets_dir / filename
            clip = _expand_bbox(blk.bbox, page.rect, pad=1.0)
            mpix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), clip=clip, alpha=False)
            mpix.save(str(out_path))
            blk.asset_path = f"assets/{filename}"

    # Drop empty text blocks (except we keep media even if tiny)
    filtered: list[Block] = []
    for blk in blocks:
        if blk.type in TEXT_TYPES and not blk.text.strip():
            continue
        if blk.type in MEDIA_TYPES and not blk.asset_path:
            continue
        filtered.append(blk)

    page_w = page.rect.width
    filtered = organize_side_by_side_floats(filtered, page_w)
    filtered = pair_formula_equation_numbers(filtered, page_w)
    ordered = sort_blocks_reading_order(filtered, page_w)
    ordered = merge_plaintext_split_by_floats(ordered)
    return merge_column_continued_text(ordered, page_w)


_EQ_NUM_RE = re.compile(r"^\s*\(\s*(\d+[a-z]?)\s*\)\s*$", re.IGNORECASE)
_MEDIA_CAPTION_TYPES = {"figure": "figure_caption", "table": "table_caption", "isolate_formula": "formula_caption"}
_CAPTION_NUMBER_RE = re.compile(r"(?i)\b(?:fig(?:ure)?\.?|table|formula|equation)\s*([0-9]+[a-z]?)")


def _is_equation_number_block(b: Block) -> bool:
    if b.type == "formula_caption":
        return bool(_EQ_NUM_RE.match((b.text or "").strip()))
    if b.type == MERGEABLE_TYPE:
        return bool(_EQ_NUM_RE.match((b.text or "").strip()))
    return False


def pair_formula_equation_numbers(
    blocks: list[Block], page_width: float
) -> list[Block]:
    """
    Pair each isolate_formula with its equation number ``(N)`` and emit as a
    float_group ordered **formula then number** (number always rendered below).

    PDF layout puts ``(N)`` on the same baseline to the right; naive y/x sort
    can place the next equation's number above its formula (e.g. ``(2)(3)``
    stacked between two formula images).
    """
    if not blocks:
        return blocks

    formulas = [
        b
        for b in blocks
        if b.type == "isolate_formula" and not b.float_group
    ]
    numbers = [
        b
        for b in blocks
        if _is_equation_number_block(b) and not b.float_group
    ]
    if not formulas or not numbers:
        return blocks

    # Promote plain-text ``(N)`` crumbs to formula_caption for consistent render
    for n in numbers:
        if n.type == MERGEABLE_TYPE:
            n.type = "formula_caption"

    used_nums: set[int] = set()
    gid = 0
    # Pair top→bottom so consecutive equations don't steal each other's labels
    for formula in sorted(formulas, key=lambda b: (b.bbox[1], b.bbox[0])):
        fx0, fy0, fx1, fy1 = formula.bbox
        fcy = (fy0 + fy1) / 2.0
        fh = max(1.0, fy1 - fy0)
        # Same-line labels sit to the right; allow small vertical slack
        max_dy = max(18.0, fh * 1.25)

        best: Block | None = None
        best_score = 1e18
        for num in numbers:
            if id(num) in used_nums:
                continue
            nx0, ny0, nx1, ny1 = num.bbox
            ncy = (ny0 + ny1) / 2.0
            dy = abs(ncy - fcy)
            if dy > max_dy:
                continue
            # Prefer labels on the right of the formula (classic AMS layout)
            right_bonus = 0.0 if nx0 >= fx0 - 2 else 40.0
            # Prefer horizontally near the right margin / formula end
            dx = abs(((nx0 + nx1) / 2.0) - fx1)
            score = dy * 3.0 + dx * 0.02 + right_bonus
            if score < best_score:
                best_score = score
                best = num

        if best is None:
            continue

        gid += 1
        group_id = f"p{formula.page}-eq{gid}"
        union = _bbox_union((formula.bbox, best.bbox))
        formula.float_group = group_id
        formula.sort_bbox = union
        formula.float_order = 0  # image first
        best.float_group = group_id
        best.sort_bbox = union
        best.float_order = 1  # number always below in MD
        # Attach for manifest / downstream
        formula.caption = _flatten_inline(best.text)
        used_nums.add(id(best))

    return blocks


def attach_media_metadata(blocks: list[Block]) -> None:
    """Assign stable media IDs and the nearest matching same-page caption."""
    used: set[str] = set()
    counters: dict[str, int] = {}
    captions_by_page: dict[int, list[Block]] = {}
    for block in blocks:
        if block.type.endswith("_caption"):
            captions_by_page.setdefault(block.page, []).append(block)
    for block in blocks:
        if block.type not in MEDIA_TYPES:
            continue
        expected = _MEDIA_CAPTION_TYPES.get(block.type)
        candidates = [c for c in captions_by_page.get(block.page, []) if expected is None or c.type == expected]
        if candidates:
            caption = min(candidates, key=lambda c: min(abs(c.bbox[1] - block.bbox[3]), abs(block.bbox[1] - c.bbox[3])))
            block.caption = _flatten_inline(caption.text)
        kind = "formula" if block.type == "isolate_formula" else block.type
        counters[kind] = counters.get(kind, 0) + 1
        match = _CAPTION_NUMBER_RE.search(block.caption) or _EQ_NUM_RE.search(
            (block.caption or "").strip()
        )
        suffix = match.group(1).lower() if match else str(counters[kind])
        candidate = f"{kind}_{suffix}"
        if candidate in used:
            candidate = f"{candidate}_{counters[kind]}"
        used.add(candidate)
        block.media_id = candidate


def write_media_manifest(blocks: list[Block], output_dir: Path) -> Path:
    media: list[dict] = []
    for block in blocks:
        if block.type not in MEDIA_TYPES or not block.asset_path:
            continue
        media.append({
            "media_id": block.media_id,
            "figure_id": block.media_id if block.type == "figure" else "",
            "type": block.type,
            "page_index": block.page,
            "page_number": block.page + 1,
            "bbox_pdf": list(block.bbox),
            "asset_path": block.asset_path,
            "image_path": block.asset_path,
            "caption": block.caption,
            "label": block.media_id.replace("_", " ").title(),
            "confidence": block.conf,
            "ready": True,
        })
    payload = {"status": "ready", "version": 1, "media": media, "figures": [m for m in media if m["type"] == "figure"]}
    path = output_dir / "media_manifest.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _font_sizes_close(a: float, b: float, rel: float = 0.15) -> bool:
    if a <= 0 or b <= 0:
        return True
    return abs(a - b) / max(a, b) <= rel


def _should_merge_plaintext(prev: Block, nxt: Block) -> bool:
    if prev.type != MERGEABLE_TYPE or nxt.type != MERGEABLE_TYPE:
        return False
    if nxt.page != prev.page + 1:
        return False
    if not _font_sizes_close(prev.font_size, nxt.font_size):
        return False

    prev_text = prev.text.rstrip()
    nxt_text = nxt.text.lstrip()
    if not prev_text or not nxt_text:
        return False

    ends_strong = prev_text.endswith(SENTENCE_END)
    starts_lower = nxt_text[:1].islower()
    starts_cont = bool(CONTINUATION_START.match(nxt_text))

    # Conservative: merge if not both "complete paragraph start/end"
    if not ends_strong:
        return True
    if starts_lower or starts_cont:
        return True
    return False


_SECTION_NUM = re.compile(r"^\d+(?:\.\d+)*$")


def merge_section_number_titles(blocks: list[Block]) -> list[Block]:
    """Join split headings like title='1' + plain_text='Introduction'."""
    if not blocks:
        return blocks
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        if (
            cur.type == "title"
            and i + 1 < len(blocks)
            and _SECTION_NUM.match(cur.text.strip())
        ):
            nxt = blocks[i + 1]
            nxt_text = nxt.text.strip()
            if (
                nxt.type in {MERGEABLE_TYPE, "title"}
                and nxt_text
                and len(nxt_text) <= 120
                and not nxt_text.endswith(SENTENCE_END)
                and "\n" not in nxt_text
            ):
                joined = f"{cur.text.strip()} {nxt_text}".strip()
                out.append(
                    Block(
                        type="title",
                        page=cur.page,
                        bbox=cur.bbox,
                        text=joined,
                        font_size=max(cur.font_size, nxt.font_size),
                        conf=max(cur.conf, nxt.conf),
                    )
                )
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def merge_cross_page_paragraphs(blocks: list[Block]) -> list[Block]:
    """Merge page-split plain_text across page boundaries only."""
    if not blocks:
        return blocks

    # Work on a flat reading-order list (already page-major from extraction)
    merged: list[Block] = []
    i = 0
    while i < len(blocks):
        cur = blocks[i]
        if (
            cur.type == MERGEABLE_TYPE
            and i + 1 < len(blocks)
            and blocks[i + 1].type == MERGEABLE_TYPE
        ):
            nxt = blocks[i + 1]
            # Only merge when they are adjacent in the stream (nothing between)
            # and satisfy cross-page heuristics.
            if _should_merge_plaintext(cur, nxt):
                joined = (cur.text.rstrip() + " " + nxt.text.lstrip()).strip()
                cur = Block(
                    type=cur.type,
                    page=cur.page,
                    bbox=cur.bbox,
                    text=joined,
                    asset_path="",
                    font_size=cur.font_size or nxt.font_size,
                    conf=max(cur.conf, nxt.conf),
                )
                merged.append(cur)
                i += 2
                continue
        merged.append(cur)
        i += 1
    return merged


def assign_heading_levels(blocks: list[Block]) -> None:
    titles = [b for b in blocks if b.type == "title" and b.font_size > 0]
    if not titles:
        for b in blocks:
            if b.type == "title":
                b.heading_level = 1
        return

    sizes = sorted({round(b.font_size, 1) for b in titles}, reverse=True)
    # Map unique sizes (largest first) into at most 3 heading levels
    if len(sizes) == 1:
        rank = {sizes[0]: 1}
    elif len(sizes) == 2:
        rank = {sizes[0]: 1, sizes[1]: 2}
    else:
        n = len(sizes)
        rank = {}
        for i, s in enumerate(sizes):
            if i < max(1, n // 3):
                rank[s] = 1
            elif i < max(2, (2 * n) // 3):
                rank[s] = 2
            else:
                rank[s] = 3

    for b in blocks:
        if b.type != "title":
            continue
        key = round(b.font_size, 1) if b.font_size > 0 else sizes[-1]
        if key not in rank:
            key = min(sizes, key=lambda s: abs(s - b.font_size))
        b.heading_level = rank[key]


def _dehyphenate_soft_breaks(text: str) -> str:
    """
    Undo PDF line-wrap hyphenation inside a block.

    - ``Shad-\\nowing`` → ``Shadowing`` (wrap)
    - ``MCP-\\nGUARD`` → ``MCP-GUARD`` (real hyphen before capitals)
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"-\n(?=[a-z])", "", text)
    text = re.sub(r"-\n(?=[A-Z])", "-", text)
    return text


def _normalize_paragraph_text(text: str) -> str:
    """Dehyphenate and collapse PDF soft wraps into one flowing paragraph."""
    text = _dehyphenate_soft_breaks(text)
    # True blank lines (rare inside one YOLO plain-text box) stay as breaks
    parts = re.split(r"\n\s*\n+", text.strip())
    normed = []
    for p in parts:
        p = re.sub(r"\s*\n\s*", " ", p)
        p = re.sub(r" +", " ", p).strip()
        if p:
            normed.append(p)
    return "\n\n".join(normed)


def _escape_md_text(text: str) -> str:
    """Render plain-text blocks; keep list structure, flatten wrapped prose."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    lines = text.split("\n")
    if any(_LIST_ITEM_START.match(ln.strip()) for ln in lines if ln.strip()):
        # Normalize soft wraps inside each list item; keep one item per line
        units = split_plain_text_units(text)
        fixed: list[str] = []
        for u in units:
            fixed.append(_normalize_paragraph_text(u))
        return "\n".join(fixed)
    return _normalize_paragraph_text(text)


def _flatten_inline(text: str) -> str:
    """Collapse newlines inside a single logical line (titles/captions)."""
    return _normalize_paragraph_text(text)


def blocks_to_markdown(blocks: list[Block]) -> str:
    parts: list[str] = []
    for blk in blocks:
        if blk.type == "title":
            level = max(1, min(3, blk.heading_level or 1))
            parts.append(f"{'#' * level} {_flatten_inline(blk.text)}")
            parts.append("")
        elif blk.type == MERGEABLE_TYPE:
            parts.append(_escape_md_text(blk.text))
            parts.append("")
        elif blk.type in MEDIA_TYPES:
            alt = blk.type.replace("_", " ")
            parts.append(f"![{alt}]({blk.asset_path})")
            parts.append("")
        elif blk.type in {
            "figure_caption",
            "table_caption",
            "table_footnote",
            "formula_caption",
        }:
            cap = _flatten_inline(blk.text)
            parts.append(f"*{cap}*")
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def build_translator(
    service: str,
    lang_in: str,
    lang_out: str,
    ignore_cache: bool = False,
    envs: Optional[dict] = None,
    prompt: Template | None = None,
) -> BaseTranslator:
    """Construct a pdf2zh translator from CLI-style ``service`` string."""
    param = service.split(":", 1)
    service_name = param[0]
    service_model = param[1] if len(param) > 1 else None
    if envs is None:
        envs = {}
    for cls in _TRANSLATOR_CLASSES:
        if service_name == cls.name:
            return cls(
                lang_in,
                lang_out,
                service_model,
                envs=envs,
                prompt=prompt,
                ignore_cache=ignore_cache,
            )
    raise ValueError(f"Unsupported translation service: {service_name}")


# Bullet / numbered list starts (PDF often uses en-dash – as bullet).
# Numbered items are 1–2 digits only — do NOT match citation years like ``2025)``.
_LIST_ITEM_START = re.compile(
    r"^(?:[\-–—*•]\s+\S|\d{1,2}[\.\)]\s+\S)"
)


def _join_soft_wrap_lines(lines: list[str]) -> str:
    """Join PDF line-wrap fragments into one string (keep hyphenation fix)."""
    if not lines:
        return ""
    chunk = lines[0].strip()
    for ln in lines[1:]:
        ln = ln.strip()
        if not ln:
            continue
        if chunk.endswith("-"):
            chunk = chunk[:-1] + ln
        else:
            chunk = chunk.rstrip() + " " + ln
    return re.sub(r" +", " ", chunk).strip()


def split_plain_text_units(text: str) -> list[str]:
    """
    Normalize plain-text soft wraps, but keep list items as separate units.

    Example: a block containing – RQ1 / – RQ2 / – RQ3 becomes 3 units so
    translation cannot glue them into one paragraph.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    units: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        nonlocal buf
        if not buf:
            return
        units.append(_join_soft_wrap_lines(buf))
        buf = []

    for ln in lines:
        raw = ln.strip()
        if not raw:
            flush()
            continue
        if _LIST_ITEM_START.match(raw):
            flush()
            buf = [raw]
        else:
            buf.append(raw)
    flush()
    return [u for u in units if u]


def _prepare_text_units(text: str, typ: str) -> list[str]:
    """Split a block into one or more translation units."""
    if typ == MERGEABLE_TYPE:
        return split_plain_text_units(text)
    flat = _flatten_inline(text)
    return [flat] if flat else []


def translate_blocks(
    blocks: list[Block],
    translator: BaseTranslator,
    thread: int = 4,
) -> list[Block]:
    """
    Translate translatable blocks in parallel; keep media / MD shell unchanged.

    Plain-text list items (e.g. – RQ1/RQ2/RQ3) are translated as separate units
    and rejoined with newlines so Markdown list structure is preserved.

    Returns a new block list (shallow-copied) with translated ``text`` fields.
    """
    out = [replace(b) for b in blocks]
    # (block_idx, unit_idx, src)
    jobs: list[tuple[int, int, str]] = []
    unit_lists: dict[int, list[str]] = {}

    for i, blk in enumerate(out):
        if blk.type not in TRANSLATABLE_TYPES:
            continue
        units = _prepare_text_units(blk.text, blk.type)
        if not units:
            continue
        unit_lists[i] = [""] * len(units)
        for u_idx, src in enumerate(units):
            jobs.append((i, u_idx, src))

    if not jobs:
        return out

    workers = max(1, int(thread or 1))
    logger.info(
        "Translating %d units (%d blocks) with %s (threads=%d)",
        len(jobs),
        len(unit_lists),
        translator,
        workers,
    )

    def _worker(item: tuple[int, int, str]) -> tuple[int, int, str]:
        b_idx, u_idx, src = item
        try:
            return b_idx, u_idx, translator.translate(src)
        except Exception:
            logger.exception(
                "Block %d unit %d translation failed; keeping source", b_idx, u_idx
            )
            return b_idx, u_idx, src

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(
            tqdm(
                executor.map(_worker, jobs),
                total=len(jobs),
                desc="markdown-translate",
            )
        )

    for b_idx, u_idx, text in results:
        unit_lists[b_idx][u_idx] = text

    for b_idx, units in unit_lists.items():
        # Preserve one list item per line for Markdown rendering
        out[b_idx].text = "\n".join(units)
    return out


def _lang_suffix(lang_out: str) -> str:
    lang = (lang_out or "zh").lower().replace("_", "-")
    if lang in {"zh", "zh-cn", "zh-hans", "chinese"}:
        return "zh"
    return re.sub(r"[^a-z0-9-]+", "", lang) or "zh"


def build_document_blocks(
    pdf_path: str | Path,
    assets_dir: Path,
    model: OnnxModel,
    pages: Optional[Iterable[int]] = None,
) -> list[Block]:
    """Detect, extract, order, and merge blocks for one PDF."""
    pdf_path = Path(pdf_path)
    doc = pymupdf.open(pdf_path)
    page_indices = list(pages) if pages is not None else list(range(doc.page_count))
    media_counters: dict[str, int] = {}
    all_blocks: list[Block] = []

    try:
        for pno in tqdm(page_indices, desc="markdown-export"):
            if pno < 0 or pno >= doc.page_count:
                continue
            page_blocks = detect_page_blocks(
                doc[pno], pno, model, assets_dir, media_counters
            )
            all_blocks.extend(page_blocks)
    finally:
        doc.close()

    all_blocks = merge_section_number_titles(all_blocks)
    # Rejoin body text split around mid-paragraph floats (incl. cross-page)
    all_blocks = merge_plaintext_split_by_floats(all_blocks)
    # Page-width unknown per block mix — column join already done per page
    all_blocks = merge_cross_page_paragraphs(all_blocks)
    assign_heading_levels(all_blocks)
    attach_media_metadata(all_blocks)
    return all_blocks


def export_pdf_to_markdown(
    pdf_path: str | Path,
    output_dir: str | Path,
    model: OnnxModel,
    pages: Optional[Iterable[int]] = None,
    translate: bool = False,
    service: str = "google",
    lang_in: str = "en",
    lang_out: str = "zh",
    thread: int = 4,
    ignore_cache: bool = False,
    envs: Optional[dict] = None,
    prompt: Template | None = None,
) -> list[Path]:
    """
    Export structured Markdown for a PDF.

    Always writes source-language ``<stem>.md``. When ``translate`` is True,
    also writes ``<stem>.<lang>.md`` (e.g. ``input.zh.md``) using block-level
    parallel translation. Image assets are shared under ``assets/``.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    all_blocks = build_document_blocks(pdf_path, assets_dir, model, pages=pages)
    write_media_manifest(all_blocks, output_dir)

    written: list[Path] = []
    md_path = output_dir / f"{pdf_path.stem}.md"
    md_path.write_text(blocks_to_markdown(all_blocks), encoding="utf-8")
    written.append(md_path)
    logger.info("Wrote markdown: %s (%d blocks)", md_path, len(all_blocks))

    if translate:
        translator = build_translator(
            service=service,
            lang_in=lang_in,
            lang_out=lang_out,
            ignore_cache=ignore_cache,
            envs=envs,
            prompt=prompt,
        )
        zh_blocks = translate_blocks(all_blocks, translator, thread=thread)
        zh_path = output_dir / f"{pdf_path.stem}.{_lang_suffix(lang_out)}.md"
        zh_path.write_text(blocks_to_markdown(zh_blocks), encoding="utf-8")
        written.append(zh_path)
        logger.info("Wrote translated markdown: %s", zh_path)

    return written


def export_files_to_markdown(
    files: list[str],
    output: str,
    model: OnnxModel,
    pages: Optional[list[int]] = None,
    translate: bool = False,
    service: str = "google",
    lang_in: str = "en",
    lang_out: str = "zh",
    thread: int = 4,
    ignore_cache: bool = False,
    envs: Optional[dict] = None,
    prompt: Template | None = None,
) -> list[Path]:
    results: list[Path] = []
    for f in files:
        path = Path(f)
        out_dir = Path(output) if output else path.parent / "md_out"
        if len(files) > 1:
            target = out_dir / path.stem
        else:
            target = out_dir
        results.extend(
            export_pdf_to_markdown(
                path,
                target,
                model,
                pages=pages,
                translate=translate,
                service=service,
                lang_in=lang_in,
                lang_out=lang_out,
                thread=thread,
                ignore_cache=ignore_cache,
                envs=envs,
                prompt=prompt,
            )
        )
    return results
