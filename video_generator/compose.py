from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .align import SubtitleSegment, write_ass
from .page_images_render import PageImageOverlaySpec
from .timeline import PageSlot
from .video_layout import LayoutRegion, VideoLayout

FPS = 25
VIDEO_CRF = 18


def _x264_encode_args() -> list[str]:
    return ["-c:v", "libx264", "-crf", str(VIDEO_CRF), "-pix_fmt", "yuv420p"]


def probe_duration_seconds(path: Path) -> float:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    duration = float(result.stdout.strip())
    if duration <= 0:
        raise RuntimeError(f"Could not read audio duration from {path}")
    return duration


def _escape_subtitles_path(path: Path) -> str:
    resolved = path.resolve().as_posix()
    return resolved.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _even_dim(value: int) -> int:
    return value if value % 2 == 0 else value + 1


def _scale_pad_filter(width: int, height: int, *, background_color: str = "white") -> str:
    even_w = _even_dim(width)
    even_h = _even_dim(height)
    return (
        f"scale={even_w}:{even_h}:force_original_aspect_ratio=decrease,"
        f"pad={even_w}:{even_h}:(ow-iw)/2:(oh-ih)/2:color={background_color},"
        "setsar=1,format=yuv420p"
    )


def _render_layout_page_clip(
    image_path: Path,
    clip_path: Path,
    *,
    canvas_width: int,
    canvas_height: int,
    page_region: LayoutRegion,
    duration: float,
    intro_sec: float = 0.0,
    background_color: str = "white",
    fps: int = FPS,
) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    even_canvas_w = _even_dim(canvas_width)
    even_canvas_h = _even_dim(canvas_height)
    page_w = _even_dim(page_region.w)
    page_h = _even_dim(page_region.h)
    duration = max(duration, 0.05)
    # Preserve the PDF aspect ratio. If it becomes taller than the selected
    # viewport, move the crop window from the top to the bottom during the slot.
    scroll_y = f"max(0,(ih-oh)*min(t/{duration:.3f},1))"
    page_chain = (
        f"fps={fps},scale={page_w}:-2,"
        f"crop={page_w}:min(ih\\,{page_h}):0:'{scroll_y}',"
        "format=rgba,"
        f"pad={page_w}:{page_h}:0:0:color=black@0,"
        "setsar=1"
    )
    if intro_sec > 0.05:
        page_chain += f",fade=t=in:st=0:d={min(0.6, intro_sec):.3f}:alpha=1"

    filter_complex = (
        f"[0:v]{page_chain}[pg];"
        f"color=c={background_color}:s={even_canvas_w}x{even_canvas_h}:d={duration:.3f}:r={fps}[bg];"
        f"[bg][pg]overlay=x={page_region.x}:y={page_region.y}:shortest=1"
    )
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-t",
        f"{duration:.3f}",
        "-i",
        str(image_path),
        "-filter_complex",
        filter_complex,
        "-r",
        str(fps),
        *_x264_encode_args(),
        "-movflags",
        "+faststart",
        str(clip_path),
    ]
    _run_ffmpeg(cmd)


def _run_ffmpeg(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def _render_static_clip(
    image_path: Path,
    clip_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    background_color: str = "white",
    fps: int = FPS,
) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-framerate",
        str(fps),
        "-t",
        f"{duration:.3f}",
        "-i",
        str(image_path),
        "-vf",
        _scale_pad_filter(width, height, background_color=background_color),
        "-r",
        str(fps),
        *_x264_encode_args(),
        "-movflags",
        "+faststart",
        str(clip_path),
    ]
    _run_ffmpeg(cmd)


def _render_intro_clip(
    image_path: Path,
    clip_path: Path,
    *,
    width: int,
    height: int,
    duration: float,
    intro_sec: float,
    background_color: str = "white",
    fps: int = FPS,
) -> None:
    clip_path.parent.mkdir(parents=True, exist_ok=True)
    even_w = _even_dim(width)
    even_h = _even_dim(height)
    intro_d = min(intro_sec, duration)
    hold_d = max(duration - intro_d, 0.0)
    intro_frames = max(int(round(intro_d * fps)), 1)
    fade_sec = min(0.75, intro_d)
    zoom_expr = f"if(lte(on\\,{intro_frames}),0.90+0.10*on/{intro_frames},1)"
    scale_pad = _scale_pad_filter(width, height, background_color=background_color)
    intro_vf = (
        f"fps={fps},{scale_pad},"
        f"zoompan=z='{zoom_expr}':"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={intro_frames}:s={even_w}x{even_h}:fps={fps},"
        f"fade=t=in:st=0:d={fade_sec:.3f}:color={background_color}"
    )

    if hold_d <= 0.05:
        _run_ffmpeg(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                str(image_path),
                "-vf",
                intro_vf,
                "-frames:v",
                str(intro_frames),
                "-r",
                str(fps),
                *_x264_encode_args(),
                "-movflags",
                "+faststart",
                str(clip_path),
            ]
        )
        return

    intro_part = clip_path.with_suffix(".intro.mp4")
    hold_part = clip_path.with_suffix(".hold.mp4")
    concat_list = clip_path.with_suffix(".concat.txt")

    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            intro_vf,
            "-frames:v",
            str(intro_frames),
            "-r",
            str(fps),
            *_x264_encode_args(),
            str(intro_part),
        ]
    )
    _render_static_clip(
        image_path,
        hold_part,
        width=width,
        height=height,
        duration=hold_d,
        background_color=background_color,
        fps=fps,
    )
    _write_clip_concat_list([intro_part, hold_part], concat_list)
    _run_ffmpeg(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(clip_path),
        ]
    )


def _write_clip_concat_list(clip_paths: list[Path], path: Path) -> None:
    lines = ["ffconcat version 1.0"]
    for clip in clip_paths:
        escaped = clip.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_ass_subtitles(
    segments: list[SubtitleSegment],
    ass_path: Path,
    *,
    width: int,
    height: int,
    font_size: int = 22,
    fade_ms: int = 0,
    margin_v: int = 28,
) -> None:
    write_ass(
        segments,
        ass_path,
        width=_even_dim(width),
        height=_even_dim(height),
        font_size=font_size,
        fade_ms=fade_ms,
        margin_v=margin_v,
    )


def load_segments_from_json(json_path: Path) -> list[SubtitleSegment]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    return [
        SubtitleSegment(
            index=int(item["index"]),
            start=float(item["start"]),
            end=float(item["end"]),
            text=str(item["text"]),
        )
        for item in payload
    ]


def _build_overlay_filter_chain(
    overlay_specs: list[PageImageOverlaySpec],
    *,
    subtitle_filter: str,
    overlay_ass_filter: str | None = None,
) -> tuple[str, list[str]]:
    """Compose: page video -> figure overlays -> title/board ASS -> bottom subtitles ASS."""
    if not overlay_specs and not overlay_ass_filter:
        return f"subtitles='{subtitle_filter}'", []

    parts: list[str] = []
    input_args: list[str] = []
    last_label = "[0:v]"

    for index, spec in enumerate(overlay_specs):
        input_args.extend(["-itsoffset", f"{spec.appear_at:.3f}", "-i", str(spec.clip_path)])
        input_index = index + 2
        out_label = f"[v{index + 1}]"
        parts.append(
            f"{last_label}[{input_index}:v]overlay=x={spec.overlay_x}:y={spec.overlay_y}:"
            f"eof_action=pass:repeatlast=0{out_label}"
        )
        last_label = out_label

    if overlay_ass_filter:
        parts.append(f"{last_label}subtitles='{overlay_ass_filter}'[vboard]")
        last_label = "[vboard]"

    parts.append(f"{last_label}subtitles='{subtitle_filter}'[vout]")
    return ";".join(parts), input_args


def compose_video(
    slots: list[PageSlot],
    audio_path: Path,
    subtitle_path: Path,
    output_path: Path,
    *,
    width: int,
    height: int,
    background_color: str = "white",
    intro_anim_sec: float = 1.0,
    subtitle_font_size: int = 22,
    subtitle_fade_ms: int = 0,
    page_image_overlays: list[PageImageOverlaySpec] | None = None,
    video_layout: VideoLayout | None = None,
    overlay_ass_path: Path | None = None,
) -> Path:
    if not slots:
        raise ValueError("No page slots provided")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    clips_dir = output_path.parent / "clips"
    clip_paths: list[Path] = []

    print(f"[compose] rendering {len(slots)} page clips", flush=True)
    for index, slot in enumerate(slots):
        duration = max(slot.end - slot.start, 0.05)
        clip_path = clips_dir / f"page_{index + 1:04d}.mp4"
        if video_layout is not None:
            _render_layout_page_clip(
                slot.image_path,
                clip_path,
                canvas_width=width,
                canvas_height=height,
                page_region=video_layout.page,
                duration=duration,
                intro_sec=intro_anim_sec if index == 0 else 0.0,
                background_color=background_color,
            )
        elif index == 0:
            _render_intro_clip(
                slot.image_path,
                clip_path,
                width=width,
                height=height,
                duration=duration,
                intro_sec=intro_anim_sec,
                background_color=background_color,
            )
        else:
            _render_static_clip(
                slot.image_path,
                clip_path,
                width=width,
                height=height,
                duration=duration,
                background_color=background_color,
            )
        clip_paths.append(clip_path)

    concat_list = output_path.parent / "clips.concat.txt"
    _write_clip_concat_list(clip_paths, concat_list)
    subtitle_filter = _escape_subtitles_path(subtitle_path)
    overlay_ass_filter = (
        _escape_subtitles_path(overlay_ass_path)
        if overlay_ass_path is not None and overlay_ass_path.is_file()
        else None
    )
    even_w = _even_dim(width)
    even_h = _even_dim(height)
    overlay_specs = page_image_overlays or []

    if overlay_specs or overlay_ass_filter:
        filter_complex, overlay_inputs = _build_overlay_filter_chain(
            overlay_specs,
            subtitle_filter=subtitle_filter,
            overlay_ass_filter=overlay_ass_filter,
        )
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-i",
            str(audio_path),
            *overlay_inputs,
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            "1:a",
            *_x264_encode_args(),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        overlay_note = f", page_images={len(overlay_specs)}"
        if overlay_ass_filter:
            overlay_note += ", board_ass=1"
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-i",
            str(audio_path),
            "-vf",
            f"subtitles='{subtitle_filter}'",
            *_x264_encode_args(),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        overlay_note = ""

    print(
        f"[compose] muxing {even_w}x{even_h} video "
        f"(intro={intro_anim_sec:.1f}s, subtitle={subtitle_font_size}px{overlay_note}) "
        f"-> {output_path}",
        flush=True,
    )
    _run_ffmpeg(cmd)
    return output_path


def remux_video_with_subtitles(
    work_dir: Path,
    audio_path: Path,
    subtitle_path: Path,
    output_path: Path,
) -> Path:
    """Re-burn subtitles on existing page clips without re-rendering."""
    concat_list = work_dir / "clips.concat.txt"
    if not concat_list.is_file():
        raise FileNotFoundError(f"Missing clip concat list: {concat_list}")

    subtitle_filter = _escape_subtitles_path(subtitle_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list),
        "-i",
        str(audio_path),
        "-vf",
        f"subtitles='{subtitle_filter}'",
        *_x264_encode_args(),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    print(f"[compose] remux with subtitles -> {output_path}", flush=True)
    _run_ffmpeg(cmd)
    return output_path
