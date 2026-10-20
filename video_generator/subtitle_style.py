from __future__ import annotations

from .subtitle_format import _measure_text

# ASS colour alpha: 00 = opaque, FF = fully transparent.
# 30% transparency -> alpha byte 0x4D (77/255).
SUBTITLE_BG_ALPHA = "&H4D&"
SUBTITLE_BG_RGB = "&H000000&"


def ass_round_rect_path(width: int, height: int, radius: int) -> str:
    radius = min(radius, width // 2, height // 2)
    if radius <= 0:
        return f"m 0 0 l {width} 0 l {width} {height} l 0 {height}"
    w = width
    h = height
    r = radius
    return (
        f"m {r} 0 "
        f"l {w - r} 0 "
        f"b {w - r} 0 {w} 0 {w} {r} "
        f"l {w} {h - r} "
        f"b {w} {h - r} {w} {h} {w - r} {h} "
        f"l {r} {h} "
        f"b {r} {h} 0 {h} 0 {h - r} "
        f"l 0 {r} "
        f"b 0 {r} 0 0 {r} 0"
    )


def estimate_subtitle_box(
    wrapped_text: str,
    *,
    font_size: int,
    canvas_width: int,
    margin_l: int,
    margin_r: int,
) -> tuple[int, int, int]:
    lines = [line for line in wrapped_text.replace("\\N", "\n").split("\n") if line]
    if not lines:
        lines = [""]

    line_height = max(int(font_size * 1.36), font_size + 4)
    pad_x = max(int(font_size * 0.45), 8)
    pad_y = max(int(font_size * 0.30), 6)
    max_line_units = max(_measure_text(line) for line in lines)
    max_line_px = int(max_line_units * font_size)
    max_allowed = max(canvas_width - margin_l - margin_r, font_size * 4)
    box_w = min(max_line_px + pad_x * 2, max_allowed)
    box_h = len(lines) * line_height + pad_y * 2
    radius = min(int(font_size * 0.28), box_h // 4, box_w // 4, 16)
    return box_w, box_h, max(radius, 4)


def build_rounded_box_override(
    *,
    canvas_width: int,
    canvas_height: int,
    margin_v: int,
    box_w: int,
    box_h: int,
    radius: int,
    fade_ms: int = 0,
) -> str:
    center_x = canvas_width // 2
    bottom_y = canvas_height - margin_v
    left = center_x - box_w // 2
    top = bottom_y - box_h
    path = ass_round_rect_path(box_w, box_h, radius)
    fade_tag = f"\\fad({fade_ms},{fade_ms})" if fade_ms > 0 else ""
    return (
        f"{{\\an7\\pos({left},{top})\\p1\\c{SUBTITLE_BG_RGB}\\1a{SUBTITLE_BG_ALPHA}"
        f"\\bord0\\shad0\\blur0{fade_tag}}}{path}{{\\p0}}"
    )


def build_text_position_override(
    *,
    canvas_width: int,
    canvas_height: int,
    margin_v: int,
    box_h: int,
) -> str:
    center_x = canvas_width // 2
    bottom_y = canvas_height - margin_v
    center_y = bottom_y - box_h // 2
    return f"\\an5\\pos({center_x},{center_y})"
