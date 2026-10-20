from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .key_points_agent import KeyPoint
from .layout import compute_pdf_page_bleed_px, scale_ui
from .video_layout import LayoutRegion, VideoLayout

TITLE_FONT_SIZE = 26
DETAIL_FONT_SIZE = 22
MAIN_TITLE_FONT_SIZE = 28
MAIN_TITLE_START_SEC = 5.0
MAIN_TITLE_Y = 48
MARGIN_LEFT = 30
MARGIN_TOP = 132
MARGIN_FROM_PAGE = 0
ITEM_GAP = 27
BOTTOM_MARGIN = 56
FADE_MS = 500
ASS_LAYER_KEY_POINTS = 2
DETAIL_TIMING_FRACTION = 1 / 3
# PingFang SC full-width CJK glyphs are ~1.0 em; 0.96 avoids premature wraps.
CJK_CHAR_WIDTH_RATIO = 0.96


@dataclass(frozen=True)
class _TextBounds:
    text_start_x: int
    right_bound_x: int
    max_width_px: int
    title_chars_per_line: int
    detail_chars_per_line: int


@dataclass(frozen=True)
class _BlockLayout:
    title_lines: list[str]
    detail_lines: list[str]
    title_h: int
    detail_h: int


def _format_ass_time(seconds: float) -> str:
    centis = int(round(seconds * 100))
    hours, rem = divmod(centis, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def _line_height(font_size: int) -> int:
    return max(int(font_size * 1.35), font_size + 4)


def _text_start_x(margin_left: int, title_font_size: int) -> int:
    return margin_left + int(title_font_size * 0.9)


def _compute_text_bounds(
    *,
    left_gutter_width: int,
    pdf_page_bleed_px: int,
    margin_left: int,
    margin_from_page: int,
    title_font_size: int,
    detail_font_size: int,
) -> _TextBounds:
    text_start_x = _text_start_x(margin_left, title_font_size)
    right_bound_x = left_gutter_width + pdf_page_bleed_px - margin_from_page
    max_width_px = max(right_bound_x - text_start_x, 80)
    return _TextBounds(
        text_start_x=text_start_x,
        right_bound_x=right_bound_x,
        max_width_px=max_width_px,
        title_chars_per_line=max(int(max_width_px / (title_font_size * CJK_CHAR_WIDTH_RATIO)), 4),
        detail_chars_per_line=max(int(max_width_px / (detail_font_size * CJK_CHAR_WIDTH_RATIO)), 4),
    )


def _compute_region_text_bounds(
    region: LayoutRegion,
    *,
    title_font_size: int,
    detail_font_size: int,
    inner_pad: int,
) -> _TextBounds:
    text_start_x = region.x + inner_pad + int(title_font_size * 0.9)
    right_bound_x = region.x + region.w - inner_pad
    max_width_px = max(right_bound_x - text_start_x, 80)
    return _TextBounds(
        text_start_x=text_start_x,
        right_bound_x=right_bound_x,
        max_width_px=max_width_px,
        title_chars_per_line=max(int(max_width_px / (title_font_size * CJK_CHAR_WIDTH_RATIO)), 4),
        detail_chars_per_line=max(int(max_width_px / (detail_font_size * CJK_CHAR_WIDTH_RATIO)), 4),
    )


def _wrap_text(text: str, max_chars_per_line: int, *, max_lines: int = 2) -> list[str]:
    def char_width(ch: str) -> float:
        # Latin glyphs are much narrower than CJK glyphs in PingFang.
        return 1.0 if ord(ch) > 0xFF else 0.56

    if sum(char_width(ch) for ch in text) <= max_chars_per_line:
        return [text]

    lines: list[str] = []
    current = ""
    current_width = 0.0
    for ch in text:
        width = char_width(ch)
        if current and current_width + width > max_chars_per_line:
            lines.append(current)
            current = ch
            current_width = width
            if len(lines) >= max_lines:
                break
        else:
            current += ch
            current_width += width
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines or [text[: max_chars_per_line * max_lines]]


def compute_detail_start(
    index: int,
    key_points: list[KeyPoint],
    video_duration: float,
) -> float:
    current = key_points[index]
    if index + 1 < len(key_points):
        next_start = key_points[index + 1].start
    else:
        next_start = video_duration
    interval = max(next_start - current.start, 1.0)
    return current.start + interval * DETAIL_TIMING_FRACTION


def build_main_title_ass_events(
    main_title: str,
    *,
    video_duration: float,
    region: LayoutRegion,
    font_size: int,
    inner_pad: int,
    alignment: str = "left",
    soft_shadow: bool = True,
    start_sec: float = MAIN_TITLE_START_SEC,
    fade_ms: int = FADE_MS,
) -> list[str]:
    if not main_title.strip():
        return []

    title_rgb = "&H2A2A2A&"
    max_width_px = max(region.w - inner_pad * 2, 80)
    available_height = max(region.h - inner_pad * 2, 40)
    fitted_font_size = font_size
    lines: list[str] = []
    minimum_font_size = max(22, int(font_size * 0.68))
    for candidate in range(font_size, minimum_font_size - 1, -1):
        chars_per_line = max(int(max_width_px / (candidate * CJK_CHAR_WIDTH_RATIO)), 4)
        candidate_lines = _wrap_text(main_title.strip(), chars_per_line, max_lines=20)
        if len(candidate_lines) <= 3 and len(candidate_lines) * candidate * 1.18 <= available_height:
            fitted_font_size = candidate
            lines = candidate_lines
            break
    if not lines:
        chars_per_line = max(int(max_width_px / (minimum_font_size * CJK_CHAR_WIDTH_RATIO)), 4)
        fitted_font_size = minimum_font_size
        lines = _wrap_text(main_title.strip(), chars_per_line, max_lines=3)
    wrapped = _escape_ass_text("\n".join(lines)).replace("\n", "\\N")
    start = _format_ass_time(start_sec)
    end = _format_ass_time(video_duration)
    if alignment == "center":
        ass_alignment = 8
        pos_x = region.x + region.w // 2
    elif alignment == "right":
        ass_alignment = 9
        pos_x = region.x + region.w - inner_pad
    else:
        ass_alignment = 7
        pos_x = region.x + inner_pad
    pos_y = region.y + inner_pad
    events: list[str] = []
    if soft_shadow:
        events.append(
            f"Dialogue: {ASS_LAYER_KEY_POINTS - 1},{start},{end},KeyPointMainTitleShadow,,0,0,0,,"
            f"{{\\an{ass_alignment}\\pos({pos_x},{pos_y + 3})\\fs{fitted_font_size}\\b1\\blur4.2"
            f"\\fad({fade_ms},{fade_ms})}}{wrapped}"
        )
    events.append(
        f"Dialogue: {ASS_LAYER_KEY_POINTS},{start},{end},KeyPointMainTitle,,0,0,0,,"
        f"{{\\an{ass_alignment}\\pos({pos_x},{pos_y})\\fs{fitted_font_size}\\b1\\c{title_rgb}"
        f"\\fad({fade_ms},{fade_ms})}}{wrapped}"
    )
    return events


def build_main_title_ass_events_legacy(
    main_title: str,
    *,
    bounds: _TextBounds,
    video_duration: float,
    margin_left: int = MARGIN_LEFT,
    start_sec: float = MAIN_TITLE_START_SEC,
    font_size: int = MAIN_TITLE_FONT_SIZE,
    margin_top_y: int = MAIN_TITLE_Y,
    fade_ms: int = FADE_MS,
) -> list[str]:
    if not main_title.strip():
        return []

    title_rgb = "&H2A2A2A&"
    max_width_px = max(bounds.right_bound_x - margin_left, 80)
    chars_per_line = max(int(max_width_px / (font_size * CJK_CHAR_WIDTH_RATIO)), 4)
    lines = _wrap_text(main_title.strip(), chars_per_line, max_lines=3)
    wrapped = _escape_ass_text("\n".join(lines)).replace("\n", "\\N")
    start = _format_ass_time(start_sec)
    end = _format_ass_time(video_duration)
    return [
        f"Dialogue: {ASS_LAYER_KEY_POINTS},{start},{end},KeyPointMainTitle,,0,0,0,,"
        f"{{\\an7\\pos({margin_left},{margin_top_y})\\fs{font_size}\\b1\\c{title_rgb}"
        f"\\fad({fade_ms},{fade_ms})}}{wrapped}"
    ]


def _plan_block_layouts(
    key_points: list[KeyPoint],
    bounds: _TextBounds,
    *,
    title_font_size: int,
    detail_font_size: int,
) -> list[_BlockLayout]:
    blocks: list[_BlockLayout] = []
    for point in key_points:
        title_lines = _wrap_text(point.title, bounds.title_chars_per_line, max_lines=2)
        detail_lines = (
            _wrap_text(point.detail, bounds.detail_chars_per_line, max_lines=2) if point.detail else []
        )
        blocks.append(
            _BlockLayout(
                title_lines=title_lines,
                detail_lines=detail_lines,
                title_h=len(title_lines) * _line_height(title_font_size),
                detail_h=len(detail_lines) * _line_height(detail_font_size) if detail_lines else 0,
            )
        )
    return blocks


def _block_height(block: _BlockLayout, title_detail_gap: int, item_gap: int) -> int:
    gap = title_detail_gap if block.detail_lines else 0
    return block.title_h + gap + block.detail_h + item_gap


def _total_layout_height(
    blocks: list[_BlockLayout],
    *,
    title_detail_gap: int,
    item_gap: int,
) -> int:
    if not blocks:
        return 0
    total = sum(_block_height(block, title_detail_gap, item_gap) for block in blocks)
    return total - item_gap


def _compute_adaptive_spacing(
    blocks: list[_BlockLayout],
    *,
    canvas_height: int,
    margin_top: int,
    bottom_margin: int,
    default_title_detail_gap: int,
    default_item_gap: int,
    min_margin_top: int,
) -> tuple[int, int, int]:
    available = canvas_height - margin_top - bottom_margin
    title_detail_gap = default_title_detail_gap
    item_gap = default_item_gap
    adjusted_margin_top = margin_top

    for _ in range(12):
        total = _total_layout_height(blocks, title_detail_gap=title_detail_gap, item_gap=item_gap)
        if total <= available:
            return title_detail_gap, item_gap, adjusted_margin_top

        scale = available / max(total, 1)
        title_detail_gap = max(int(title_detail_gap * scale), 5)
        item_gap = max(int(item_gap * scale), 5)

    total = _total_layout_height(blocks, title_detail_gap=title_detail_gap, item_gap=item_gap)
    if total > available and adjusted_margin_top > min_margin_top:
        adjusted_margin_top = max(int(adjusted_margin_top - (total - available) * 0.5), min_margin_top)

    return title_detail_gap, item_gap, adjusted_margin_top


def _layout_dims(canvas_height: int) -> dict[str, int]:
    return {
        "title_font_size": scale_ui(TITLE_FONT_SIZE, canvas_height),
        "detail_font_size": scale_ui(DETAIL_FONT_SIZE, canvas_height),
        "main_title_font_size": scale_ui(MAIN_TITLE_FONT_SIZE, canvas_height),
        "main_title_y": scale_ui(MAIN_TITLE_Y, canvas_height),
        "margin_left": scale_ui(MARGIN_LEFT, canvas_height),
        "margin_top": scale_ui(MARGIN_TOP, canvas_height),
        "item_gap": scale_ui(ITEM_GAP, canvas_height),
        "bottom_margin": scale_ui(BOTTOM_MARGIN, canvas_height),
    }


def build_key_points_ass_events(
    key_points: list[KeyPoint],
    *,
    canvas_width: int,
    canvas_height: int,
    video_duration: float,
    video_layout: VideoLayout | None = None,
    left_gutter_width: int = 0,
    pdf_page_bleed_px: int = 0,
    main_title: str | None = None,
    title_font_size: int | None = None,
    detail_font_size: int | None = None,
    margin_left: int | None = None,
    margin_top: int | None = None,
    margin_from_page: int = MARGIN_FROM_PAGE,
    item_gap: int | None = None,
    fade_ms: int = FADE_MS,
) -> list[str]:
    dims = _layout_dims(canvas_height)
    title_font_size = title_font_size if title_font_size is not None else dims["title_font_size"]
    detail_font_size = detail_font_size if detail_font_size is not None else dims["detail_font_size"]
    inner_pad = scale_ui(12, canvas_height)
    events: list[str] = []

    if video_layout is not None:
        title_font_size = video_layout.key_points_font_size
        detail_font_size = max(18, round(title_font_size * 0.82))
        title_region = video_layout.title
        kp_region = video_layout.key_points
        bounds = _compute_region_text_bounds(
            kp_region,
            title_font_size=title_font_size,
            detail_font_size=detail_font_size,
            inner_pad=inner_pad,
        )
        bullet_x = kp_region.x + inner_pad
        margin_top = kp_region.y + inner_pad
        bottom_margin = inner_pad
        min_margin_top = kp_region.y + inner_pad
        item_gap = item_gap if item_gap is not None else dims["item_gap"]

        if main_title:
            events.extend(
                build_main_title_ass_events(
                    main_title,
                    video_duration=video_duration,
                    region=title_region,
                    font_size=video_layout.title_font_size,
                    inner_pad=inner_pad,
                    alignment=video_layout.title_alignment,
                    soft_shadow=video_layout.title_shadow,
                    fade_ms=fade_ms,
                )
            )
            print(
                f"[key_points] main title at {MAIN_TITLE_START_SEC:.0f}s in title region "
                f"({title_region.x},{title_region.y},{title_region.w}x{title_region.h})",
                flush=True,
            )
    else:
        margin_left = margin_left if margin_left is not None else dims["margin_left"]
        margin_top = margin_top if margin_top is not None else dims["margin_top"]
        item_gap = item_gap if item_gap is not None else dims["item_gap"]
        bounds = _compute_text_bounds(
            left_gutter_width=left_gutter_width,
            pdf_page_bleed_px=pdf_page_bleed_px,
            margin_left=margin_left,
            margin_from_page=margin_from_page,
            title_font_size=title_font_size,
            detail_font_size=detail_font_size,
        )
        bottom_margin = dims["bottom_margin"]
        min_margin_top = scale_ui(80, canvas_height)
        bullet_x = margin_left

        if main_title:
            events.extend(
                build_main_title_ass_events_legacy(
                    main_title,
                    bounds=bounds,
                    video_duration=video_duration,
                    margin_left=margin_left,
                    font_size=dims["main_title_font_size"],
                    margin_top_y=dims["main_title_y"],
                    fade_ms=fade_ms,
                )
            )
            print(
                f"[key_points] main title at {MAIN_TITLE_START_SEC:.0f}s, "
                f"{dims['main_title_font_size']}px",
                flush=True,
            )

    if not key_points:
        return events

    blocks = _plan_block_layouts(
        key_points,
        bounds,
        title_font_size=title_font_size,
        detail_font_size=detail_font_size,
    )
    default_title_detail_gap = max(_line_height(detail_font_size) // 2, 8)
    if video_layout is not None:
        kp_region = video_layout.key_points
        spacing_top = inner_pad
        spacing_height = kp_region.h
        spacing_bottom = inner_pad
        min_spacing_top = inner_pad
    else:
        spacing_top = margin_top
        spacing_height = canvas_height
        spacing_bottom = bottom_margin
        min_spacing_top = min_margin_top

    title_detail_gap, item_gap, adjusted_top = _compute_adaptive_spacing(
        blocks,
        canvas_height=spacing_height,
        margin_top=spacing_top,
        bottom_margin=spacing_bottom,
        default_title_detail_gap=default_title_detail_gap,
        default_item_gap=item_gap,
        min_margin_top=min_spacing_top,
    )
    if video_layout is not None:
        margin_top = kp_region.y + adjusted_top
    else:
        margin_top = adjusted_top

    if video_layout is not None:
        print(
            f"[key_points] layout region key_points=({kp_region.x},{kp_region.y},"
            f"{kp_region.w}x{kp_region.h}) width={bounds.max_width_px}px",
            flush=True,
        )
    else:
        gap_to_page_image = left_gutter_width - bounds.right_bound_x
        print(
            f"[key_points] layout bounds: text_x={bounds.text_start_x} "
            f"right_x={bounds.right_bound_x} (page_image_x={left_gutter_width}, "
            f"bleed={pdf_page_bleed_px}px, gap_to_page_image={gap_to_page_image}px), "
            f"width={bounds.max_width_px}px (~{bounds.detail_chars_per_line} chars/line), "
            f"fonts title={title_font_size}/detail={detail_font_size}",
            flush=True,
        )

    title_rgb = "&H2A2A2A&"
    detail_rgb = "&H2A2A2A&"
    bullet_rgb = "&H4080FF&"
    board_shadow = video_layout is None or video_layout.key_points_shadow

    cursor_y = margin_top
    end = _format_ass_time(video_duration)

    for index, (point, block) in enumerate(zip(key_points, blocks, strict=True)):
        title_start = _format_ass_time(point.start)
        detail_start = _format_ass_time(compute_detail_start(index, key_points, video_duration))

        if board_shadow:
            events.append(
                f"Dialogue: {ASS_LAYER_KEY_POINTS - 1},{title_start},{end},KeyPointTitleShadow,,0,0,0,,"
                f"{{\\an7\\pos({bullet_x},{cursor_y + 2})\\fs{title_font_size}\\blur3.6"
                f"\\fad({fade_ms},{fade_ms})}}▸"
            )
        events.append(
            f"Dialogue: {ASS_LAYER_KEY_POINTS},{title_start},{end},KeyPointTitle,,0,0,0,,"
            f"{{\\an7\\pos({bullet_x},{cursor_y})\\fs{title_font_size}\\c{bullet_rgb}"
            f"\\fad({fade_ms},{fade_ms})}}▸"
        )

        title_text = _escape_ass_text("\n".join(block.title_lines)).replace("\n", "\\N")
        if board_shadow:
            events.append(
                f"Dialogue: {ASS_LAYER_KEY_POINTS - 1},{title_start},{end},KeyPointTitleShadow,,0,0,0,,"
                f"{{\\an7\\pos({bounds.text_start_x},{cursor_y + 2})\\fs{title_font_size}\\b1\\blur3.6"
                f"\\fad({fade_ms},{fade_ms})}}{title_text}"
            )
        events.append(
            f"Dialogue: {ASS_LAYER_KEY_POINTS},{title_start},{end},KeyPointTitle,,0,0,0,,"
            f"{{\\an7\\pos({bounds.text_start_x},{cursor_y})\\fs{title_font_size}\\b1\\c{title_rgb}"
            f"\\fad({fade_ms},{fade_ms})}}{title_text}"
        )

        if block.detail_lines:
            detail_y = cursor_y + block.title_h + title_detail_gap
            detail_text = _escape_ass_text("\n".join(block.detail_lines)).replace("\n", "\\N")
            if board_shadow:
                events.append(
                    f"Dialogue: {ASS_LAYER_KEY_POINTS - 1},{detail_start},{end},KeyPointDetailShadow,,0,0,0,,"
                    f"{{\\an7\\pos({bounds.text_start_x},{detail_y + 2})\\fs{detail_font_size}\\blur3.2"
                    f"\\fad({fade_ms},{fade_ms})}}{detail_text}"
                )
            events.append(
                f"Dialogue: {ASS_LAYER_KEY_POINTS},{detail_start},{end},KeyPointDetail,,0,0,0,,"
                f"{{\\an7\\pos({bounds.text_start_x},{detail_y})\\fs{detail_font_size}\\c{detail_rgb}"
                f"\\fad({fade_ms},{fade_ms})}}{detail_text}"
            )

        gap = title_detail_gap if block.detail_lines else 0
        cursor_y += block.title_h + gap + block.detail_h + item_gap

    return events


def write_key_points_ass(
    key_points: list[KeyPoint],
    path: Path,
    *,
    canvas_width: int,
    canvas_height: int,
    video_duration: float,
    video_layout: VideoLayout | None = None,
    left_gutter_width: int = 0,
    page_width: int = 0,
    page_height: int = 0,
    main_title: str | None = None,
    title_font_size: int | None = None,
    detail_font_size: int | None = None,
) -> None:
    dims = _layout_dims(canvas_height)
    title_font_size = title_font_size if title_font_size is not None else dims["title_font_size"]
    detail_font_size = detail_font_size if detail_font_size is not None else dims["detail_font_size"]
    main_title_font_size = dims["main_title_font_size"]
    title_shadow_enabled = video_layout.title_shadow if video_layout is not None else True
    key_points_shadow_enabled = video_layout.key_points_shadow if video_layout is not None else True
    pdf_page_bleed_px = 0
    if video_layout is None and page_width > 0 and page_height > 0:
        pdf_page_bleed_px = compute_pdf_page_bleed_px(
            page_width,
            page_height,
            canvas_width,
            canvas_height,
        )
    title_style = (
        f"Style: KeyPointTitle,PingFang SC,{title_font_size},"
        "&H2A2A2A,&H2A2A2A,&H000000,&HFF000000,"
        "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
    )
    detail_style = (
        f"Style: KeyPointDetail,PingFang SC,{detail_font_size},"
        "&H2A2A2A,&H2A2A2A,&H000000,&HFF000000,"
        "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
    )
    main_title_style = (
        f"Style: KeyPointMainTitle,PingFang SC,{main_title_font_size},"
        "&H2A2A2A,&H2A2A2A,&H000000,&HFF000000,"
        "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
    )
    styles = [title_style, detail_style]
    if key_points_shadow_enabled:
        styles.extend([
            f"Style: KeyPointTitleShadow,PingFang SC,{title_font_size},"
            "&HCA000000,&HCA000000,&HFF000000,&HFF000000,"
            "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
            f"Style: KeyPointDetailShadow,PingFang SC,{detail_font_size},"
            "&HDC000000,&HDC000000,&HFF000000,&HFF000000,"
            "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        ])
    if main_title:
        styles.append(main_title_style)
        if title_shadow_enabled:
            styles.append(
                f"Style: KeyPointMainTitleShadow,PingFang SC,{main_title_font_size},"
                "&HC4000000,&HC4000000,&HFF000000,&HFF000000,"
                "0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1"
            )
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {canvas_width}",
            f"PlayResY: {canvas_height}",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            *styles,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    events = build_key_points_ass_events(
        key_points,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        video_duration=video_duration,
        video_layout=video_layout,
        left_gutter_width=left_gutter_width,
        pdf_page_bleed_px=pdf_page_bleed_px,
        main_title=main_title,
        title_font_size=title_font_size,
        detail_font_size=detail_font_size,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + "\n" + "\n".join(events) + "\n", encoding="utf-8")


def merge_ass_files(base_ass: Path, overlay_ass: Path, output_ass: Path) -> None:
    base_text = base_ass.read_text(encoding="utf-8")
    overlay_text = overlay_ass.read_text(encoding="utf-8")

    overlay_events_match = re.search(r"\[Events\]\s*\nFormat:[^\n]*\n(.*)", overlay_text, re.DOTALL)
    if not overlay_events_match:
        raise ValueError(f"No [Events] section in {overlay_ass}")

    overlay_dialogues = [
        line
        for line in overlay_events_match.group(1).splitlines()
        if line.startswith("Dialogue:")
    ]
    if not overlay_dialogues:
        output_ass.write_text(base_text, encoding="utf-8")
        return

    merged = base_text.rstrip() + "\n" + "\n".join(overlay_dialogues) + "\n"
    output_ass.parent.mkdir(parents=True, exist_ok=True)
    output_ass.write_text(merged, encoding="utf-8")
