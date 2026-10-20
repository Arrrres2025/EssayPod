from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .layout import compute_right_gutter, compute_scaled_page_size, scale_ui
from .pdf_images import PageImageEvent
from .video_layout import LayoutRegion, VideoLayout

MARGIN_RIGHT = 30
MARGIN_TOP = 132
BOTTOM_MARGIN = 56
ITEM_GAP = 24
ANIM_SEC = 1.0
SLIDE_PX = 72
FPS = 25


@dataclass(frozen=True)
class PageImageOverlaySpec:
    clip_path: Path
    appear_at: float
    overlay_x: int
    overlay_y: int


@dataclass(frozen=True)
class _PlacedImage:
    event: PageImageEvent
    x: int
    y: int
    width: int
    height: int


def _even_dim(value: int) -> int:
    return value if value % 2 == 0 else value + 1


def _fit_size(
    native_w: int,
    native_h: int,
    max_w: int,
    max_h: int,
) -> tuple[int, int]:
    if native_w <= 0 or native_h <= 0:
        return max_w, max_h
    scale = min(max_w / native_w, max_h / native_h)
    return _even_dim(max(1, int(round(native_w * scale)))), _even_dim(max(1, int(round(native_h * scale))))


def _compute_adaptive_item_gap(
    placed: list[_PlacedImage],
    *,
    canvas_height: int,
    margin_top: int,
    bottom_margin: int,
    default_gap: int,
    min_margin_top: int,
) -> tuple[int, int]:
    if not placed:
        return default_gap, margin_top

    available = canvas_height - margin_top - bottom_margin
    total_h = sum(item.height for item in placed)
    gap_count = max(len(placed) - 1, 0)
    if gap_count == 0:
        return default_gap, margin_top

    needed = total_h + default_gap * gap_count
    if needed <= available:
        return default_gap, margin_top

    gap = max(int((available - total_h) / gap_count), 8)
    margin_top_adj = margin_top
    if total_h + gap * gap_count > available and margin_top_adj > min_margin_top:
        margin_top_adj = max(
            int(margin_top_adj - (total_h + gap * gap_count - available) * 0.35),
            min_margin_top,
        )
    return gap, margin_top_adj


def plan_page_image_layout(
    events: list[PageImageEvent],
    *,
    canvas_width: int,
    canvas_height: int,
    page_width: int,
    page_height: int,
    figures_region: LayoutRegion | None = None,
) -> list[_PlacedImage]:
    if not events:
        return []

    if figures_region is not None:
        margin_right = scale_ui(12, canvas_height)
        margin_top = scale_ui(12, canvas_height)
        bottom_margin = scale_ui(12, canvas_height)
        item_gap = scale_ui(ITEM_GAP, canvas_height)
        min_margin_top = margin_top
        usable_w = max(figures_region.w - margin_right * 2, 80)
        usable_h = figures_region.h - margin_top - bottom_margin
        base_x = figures_region.x + margin_right
        base_y = figures_region.y + margin_top
        right_gutter = figures_region.w
    else:
        margin_right = scale_ui(MARGIN_RIGHT, canvas_height)
        margin_top = scale_ui(MARGIN_TOP, canvas_height)
        bottom_margin = scale_ui(BOTTOM_MARGIN, canvas_height)
        item_gap = scale_ui(ITEM_GAP, canvas_height)
        min_margin_top = scale_ui(80, canvas_height)

        right_gutter = _even_dim(
            compute_right_gutter(page_width, page_height, canvas_width, canvas_height)
        )
        scaled_w, _ = compute_scaled_page_size(page_width, page_height, canvas_width, canvas_height)
        left_gutter = max((canvas_width - scaled_w) // 2, 0)
        page_right_x = left_gutter + scaled_w
        usable_w = max(right_gutter - margin_right * 2, 80)
        usable_h = canvas_height - margin_top - bottom_margin
        base_x = page_right_x + margin_right
        base_y = margin_top

    if figures_region is not None:
        # Figure is a temporary stage: every event independently fills the
        # configured region and is centered, rather than being accumulated.
        placed: list[_PlacedImage] = []
        for event in events:
            width, height = _fit_size(event.native_width, event.native_height, usable_w, usable_h)
            placed.append(_PlacedImage(
                event=event,
                x=figures_region.x + max((figures_region.w - width) // 2, 0),
                y=figures_region.y + max((figures_region.h - height) // 2, 0),
                width=width,
                height=height,
            ))
        return placed

    per_item_max_h = max(int(usable_h / max(len(events), 1) - item_gap * 0.5), 80)
    placed: list[_PlacedImage] = []
    for event in events:
        width, height = _fit_size(event.native_width, event.native_height, usable_w, per_item_max_h)
        placed.append(_PlacedImage(event=event, x=base_x, y=0, width=width, height=height))

    region_height = figures_region.h if figures_region is not None else canvas_height
    for _ in range(8):
        item_gap, layout_margin_top = _compute_adaptive_item_gap(
            placed,
            canvas_height=region_height,
            margin_top=margin_top,
            bottom_margin=bottom_margin,
            default_gap=item_gap,
            min_margin_top=min_margin_top,
        )
        total_h = sum(item.height for item in placed) + item_gap * max(len(placed) - 1, 0)
        available = region_height - layout_margin_top - bottom_margin
        if total_h <= available:
            margin_top = layout_margin_top
            break
        per_item_max_h = max(int(per_item_max_h * 0.88), 64)
        placed = []
        for event in events:
            width, height = _fit_size(
                event.native_width,
                event.native_height,
                usable_w,
                per_item_max_h,
            )
            placed.append(_PlacedImage(event=event, x=base_x, y=0, width=width, height=height))

    item_gap, layout_margin_top = _compute_adaptive_item_gap(
        placed,
        canvas_height=region_height,
        margin_top=margin_top,
        bottom_margin=bottom_margin,
        default_gap=item_gap,
        min_margin_top=min_margin_top,
    )
    margin_top = layout_margin_top

    cursor_y = (figures_region.y if figures_region else 0) + margin_top
    laid_out: list[_PlacedImage] = []
    for item in placed:
        laid_out.append(
            _PlacedImage(
                event=item.event,
                x=item.x,
                y=cursor_y,
                width=item.width,
                height=item.height,
            )
        )
        cursor_y += item.height + item_gap

    total_h = cursor_y - item_gap - (figures_region.y + margin_top if figures_region else margin_top) if laid_out else 0
    print(
        f"[page_images] layout: count={len(laid_out)} gutter={right_gutter}px "
        f"usable={usable_w}x{usable_h} total_h={total_h} gap={item_gap}",
        flush=True,
    )
    for index, item in enumerate(laid_out, start=1):
        print(
            f"[page_images]   #{index} page={item.event.page_index + 1} "
            f"at={item.event.appear_at:.2f}s pos=({item.x},{item.y}) "
            f"size={item.width}x{item.height}",
            flush=True,
        )
    return laid_out


def _run_ffmpeg(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _render_slide_overlay_clip(
    image_path: Path,
    clip_path: Path,
    *,
    panel_width: int,
    panel_height: int,
    image_x_in_panel: int,
    image_y_in_panel: int,
    width: int,
    height: int,
    duration: float,
    anim_sec: float = ANIM_SEC,
    slide_px: int = SLIDE_PX,
    fps: int = FPS,
) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(duration, 0.05)
    panel_width = _even_dim(panel_width)
    panel_height = _even_dim(panel_height)
    width = _even_dim(width)
    height = _even_dim(height)
    slide_px = min(slide_px, max(image_x_in_panel + 1, 1))
    anim_sec = min(anim_sec, duration)

    fade_out_start = max(duration - anim_sec, 0.0)
    filter_complex = (
        f"[1:v]scale={width}:{height},format=rgba,"
        f"fade=t=in:st=0:d={anim_sec:.3f}:alpha=1,"
        f"fade=t=out:st={fade_out_start:.3f}:d={anim_sec:.3f}:alpha=1[img];"
        f"color=c=white:s={panel_width}x{panel_height}:d={duration:.3f}:r={fps}[panel];"
        f"[panel][img]overlay=x={image_x_in_panel}:y={image_y_in_panel}:eval=frame:shortest=1"
    )

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=white:s={panel_width}x{panel_height}:d={duration:.3f}:r={fps}",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-filter_complex",
        filter_complex,
        "-t",
        f"{duration:.3f}",
        "-r",
        str(fps),
        "-c:v",
        "libx264",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(clip_path),
    ]
    _run_ffmpeg(cmd)


def _render_focus_overlay_clip(
    image_path: Path,
    clip_path: Path,
    *,
    canvas_width: int,
    canvas_height: int,
    page_region: LayoutRegion,
    image_x: int,
    image_y: int,
    width: int,
    height: int,
    duration: float,
    anim_sec: float = 0.45,
    fps: int = FPS,
) -> None:
    """Render one finite, alpha-preserving Figure focus layer.

    The layer contains only a 20% page dimmer and the Figure itself. Both
    animate on the alpha channel, so everything outside those rectangles stays
    transparent and the clip disappears cleanly at EOF.
    """
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    duration = max(duration, 0.05)
    anim_sec = min(anim_sec, duration / 2)
    fade_out_start = max(duration - anim_sec, 0.0)
    canvas_width = _even_dim(canvas_width)
    canvas_height = _even_dim(canvas_height)
    width = _even_dim(width)
    height = _even_dim(height)
    page_w = _even_dim(page_region.w)
    page_h = _even_dim(page_region.h)
    filter_complex = (
        f"color=c=black@0:s={canvas_width}x{canvas_height}:d={duration:.3f}:r={fps},format=rgba[base];"
        f"color=c=black@0.20:s={page_w}x{page_h}:d={duration:.3f}:r={fps},format=rgba,"
        f"fade=t=in:st=0:d={anim_sec:.3f}:alpha=1,"
        f"fade=t=out:st={fade_out_start:.3f}:d={anim_sec:.3f}:alpha=1[shade];"
        f"[0:v]fps={fps},scale={width}:{height},format=rgba,"
        f"fade=t=in:st=0:d={anim_sec:.3f}:alpha=1,"
        f"fade=t=out:st={fade_out_start:.3f}:d={anim_sec:.3f}:alpha=1[img];"
        f"[base][shade]overlay=x={page_region.x}:y={page_region.y}:shortest=1[dimmed];"
        f"[dimmed][img]overlay=x={image_x}:y={image_y}:shortest=1,format=rgba[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-framerate", str(fps),
        "-t", f"{duration:.3f}", "-i", str(image_path),
        "-filter_complex", filter_complex,
        "-map", "[out]", "-t", f"{duration:.3f}", "-r", str(fps),
        "-c:v", "qtrle", "-pix_fmt", "argb", str(clip_path),
    ]
    _run_ffmpeg(cmd)


def render_page_image_overlays(
    events: list[PageImageEvent],
    clips_dir: Path,
    *,
    canvas_width: int,
    canvas_height: int,
    page_width: int,
    page_height: int,
    video_duration: float,
    video_layout: VideoLayout | None = None,
) -> list[PageImageOverlaySpec]:
    figures_region = video_layout.figures if video_layout is not None else None
    placed = plan_page_image_layout(
        events,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
        page_width=page_width,
        page_height=page_height,
        figures_region=figures_region,
    )
    if not placed:
        return []

    if figures_region is not None:
        panel_origin_x = figures_region.x
    else:
        right_gutter = _even_dim(
            compute_right_gutter(page_width, page_height, canvas_width, canvas_height)
        )
        scaled_w, _ = compute_scaled_page_size(page_width, page_height, canvas_width, canvas_height)
        left_gutter = max((canvas_width - scaled_w) // 2, 0)
        page_right_x = left_gutter + scaled_w
        panel_origin_x = page_right_x

    clips_dir.mkdir(parents=True, exist_ok=True)
    overlay_specs: list[PageImageOverlaySpec] = []

    for index, item in enumerate(placed, start=1):
        clip_duration = max(item.event.disappear_at - item.event.appear_at, 0.05)
        clip_path = clips_dir / f"page_image_{index:02d}.mov"
        if figures_region is not None:
            _render_focus_overlay_clip(
                item.event.image_path,
                clip_path,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                page_region=video_layout.page,
                image_x=item.x,
                image_y=item.y,
                width=item.width,
                height=item.height,
                duration=clip_duration,
            )
            overlay_specs.append(PageImageOverlaySpec(
                clip_path=clip_path,
                appear_at=item.event.appear_at,
                overlay_x=0,
                overlay_y=0,
            ))
            continue
        else:
            panel_width = _even_dim(
                compute_right_gutter(page_width, page_height, canvas_width, canvas_height)
            )
            image_x_in_panel = item.x - panel_origin_x
            image_y_in_panel = item.y
            panel_height = _even_dim(canvas_height)
            overlay_y = 0
        _render_slide_overlay_clip(
            item.event.image_path,
            clip_path,
            panel_width=panel_width,
            panel_height=panel_height,
            image_x_in_panel=image_x_in_panel,
            image_y_in_panel=image_y_in_panel,
            width=item.width,
            height=item.height,
            duration=clip_duration,
            slide_px=scale_ui(SLIDE_PX, canvas_height),
        )
        overlay_specs.append(
            PageImageOverlaySpec(
                clip_path=clip_path,
                appear_at=item.event.appear_at,
                overlay_x=panel_origin_x if figures_region is not None else panel_origin_x,
                overlay_y=overlay_y,
            )
        )

    return overlay_specs
