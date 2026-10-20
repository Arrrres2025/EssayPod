from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .align import SubtitleSegment, align_subtitles_with_whisper, write_ass, write_subtitle_json
from .audio_effects import mix_key_point_bubbles
from .clause_timestamps import expand_segments_by_clauses
from .compose import compose_video, probe_duration_seconds, load_segments_from_json
from .condensed import parse_condensed_title
from .key_points_agent import KeyPoint, load_key_points_json, try_generate_key_points
from .key_points_render import merge_ass_files, write_key_points_ass
from .layout import compute_left_gutter
from .page_images_render import PageImageOverlaySpec, render_page_image_overlays
from .pdf_images import (
    build_page_image_events,
    load_document_media,
    write_page_images_json,
)
from .pdf_pages import render_pdf_pages
from .podcast_timestamps import build_subtitles_from_podcast_timestamps
from .timeline import build_equal_page_timeline, write_concat_demuxer
from .transcript import parse_transcript, transcript_to_alignment_text
from .video_presets import VideoPreset, get_video_preset
from .video_layout import VideoLayout, resolve_layout


def _write_ass_for_preset(
    segments: list[SubtitleSegment],
    ass_path: Path,
    preset: VideoPreset,
    *,
    clauses_json: Path | None = None,
    subtitle_fade_ms: int | None = None,
    video_layout: VideoLayout | None = None,
) -> None:
    clause_segments = expand_segments_by_clauses(segments)
    if clauses_json is not None:
        write_subtitle_json(clause_segments, clauses_json)
    print(
        f"[subtitles] expanded {len(segments)} rounds -> {len(clause_segments)} clause cues",
        flush=True,
    )
    write_ass(
        clause_segments,
        ass_path,
        width=preset.width,
        height=preset.height,
        font_size=video_layout.subtitle_font_size if video_layout is not None else preset.subtitle_font_size,
        margin_v=video_layout.subtitle_margin_v if video_layout is not None else preset.subtitle_margin_v,
        margin_l=preset.subtitle_margin_lr,
        margin_r=preset.subtitle_margin_lr,
        fade_ms=preset.subtitle_fade_ms if subtitle_fade_ms is None else subtitle_fade_ms,
        first_subtitle_fade_ms=preset.first_subtitle_fade_ms,
    )


def _resolve_burn_ass_path(
    *,
    preset: VideoPreset,
    page_width: int,
    page_height: int,
    round_segments: list[SubtitleSegment],
    audio_duration: float,
    work_dir: Path,
    subtitle_ass: Path,
    skip_key_points: bool,
    task_dir: Path,
    audio_path: Path,
    video_layout: VideoLayout | None = None,
) -> tuple[Path, Path | None, Path]:
    if preset.name != "16:9":
        return subtitle_ass, None, audio_path

    if video_layout is not None:
        board_min_width = video_layout.key_points.w
    else:
        board_min_width = compute_left_gutter(
            page_width, page_height, preset.width, preset.height
        )
    left_gutter = board_min_width if video_layout is not None else compute_left_gutter(
        page_width, page_height, preset.width, preset.height
    )
    key_points_json = work_dir / "key_points.json"
    key_points_ass = work_dir / "key_points.ass"
    merged_ass = work_dir / "video.ass"
    main_title = parse_condensed_title(task_dir / "condensed.txt")

    key_points: list[KeyPoint] | None = None
    if skip_key_points:
        if key_points_json.is_file():
            try:
                key_points = load_key_points_json(key_points_json)
                print(f"[key_points] reusing cached {len(key_points)} anchors", flush=True)
            except Exception as exc:
                print(f"[key_points] skipped: invalid cache ({exc})", flush=True)
    else:
        key_points = try_generate_key_points(
            round_segments,
            key_points_json,
            left_gutter_width=board_min_width,
        )

    if not key_points and not main_title:
        return subtitle_ass, None, audio_path

    write_key_points_ass(
        key_points or [],
        key_points_ass,
        canvas_width=preset.width,
        canvas_height=preset.height,
        video_duration=audio_duration,
        video_layout=video_layout,
        left_gutter_width=left_gutter,
        page_width=page_width,
        page_height=page_height,
        main_title=main_title,
    )
    merge_ass_files(subtitle_ass, key_points_ass, merged_ass)
    print(f"[key_points] merged subtitles + key points -> {merged_ass}", flush=True)

    output_audio = audio_path
    if key_points:
        mixed_audio = work_dir / "podcast_with_bubbles.mp3"
        mixed = mix_key_point_bubbles(
            audio_path,
            [point.start for point in key_points],
            mixed_audio,
        )
        if mixed is not None:
            output_audio = mixed

    return subtitle_ass, key_points_ass, output_audio


def generate_video(
    task_dir: Path,
    *,
    output_path: Path | None = None,
    work_dir: Path | None = None,
    video_format: str = "16:9",
    whisper_model: str = "small",
    pdf_dpi: int = 200,
    max_pages: int | None = None,
    skip_align: bool = False,
    align_mode: str = "auto",
    subtitle_source: str = "podcast",
    subtitle_fade_ms: int = 0,
    skip_key_points: bool = False,
    skip_page_images: bool = False,
    progress_callback: Callable[[int, str, str], None] | None = None,
) -> Path:
    def progress(value: int, stage: str, message: str) -> None:
        if progress_callback is not None:
            progress_callback(value, stage, message)

    task_dir = task_dir.resolve()
    pdf_path = task_dir / "input.pdf"
    audio_path = task_dir / "podcast.mp3"
    transcript_path = task_dir / "transcript.txt"

    for required in (pdf_path, audio_path, transcript_path):
        if not required.is_file():
            raise FileNotFoundError(f"Missing required file: {required}")

    work_dir = (work_dir or task_dir / "video").resolve()
    pages_dir = work_dir / "pages"
    concat_file = work_dir / "pages.concat.txt"
    subtitle_srt = work_dir / "subtitles.srt"
    subtitle_ass = work_dir / "subtitles.ass"
    subtitle_json = work_dir / "subtitles.json"
    subtitle_clauses_json = work_dir / "subtitles_clauses.json"
    timeline_json = work_dir / "page_timeline.json"
    output_path = (output_path or work_dir / "paper_video.mp4").resolve()

    preset = get_video_preset(video_format)

    progress(3, "preparing", "正在检查论文、音频和逐字稿")
    print(f"[pipeline] task={task_dir.name}", flush=True)
    print(
        f"[pipeline] video format={preset.name} ({preset.width}x{preset.height}), "
        f"subtitle={preset.subtitle_font_size}px",
        flush=True,
    )
    print(f"[pipeline] rendering PDF pages (dpi={pdf_dpi})", flush=True)
    progress(10, "rendering", "正在渲染论文页面")
    page_result = render_pdf_pages(
        pdf_path, pages_dir, dpi=pdf_dpi, max_pages=max_pages, stop_at_references=False
    )

    layout_path = work_dir / "layout.json"
    video_layout: VideoLayout | None = None
    if preset.name == "16:9":
        video_layout = resolve_layout(
            layout_path=layout_path,
            canvas_width=preset.width,
            canvas_height=preset.height,
            page_width=page_result.width,
            page_height=page_result.height,
        )
        print(f"[layout] using regions from {layout_path}", flush=True)

    audio_duration = probe_duration_seconds(audio_path)
    progress(24, "subtitles", "正在对齐字幕与播客时间轴")
    print(f"[pipeline] audio duration={audio_duration:.2f}s", flush=True)

    transcript_lines = parse_transcript(transcript_path)
    alignment_text = transcript_to_alignment_text(transcript_lines)

    segments: list[SubtitleSegment] | None = None
    if skip_align and subtitle_ass.is_file():
        print(f"[pipeline] reusing existing subtitles: {subtitle_ass}", flush=True)
        if subtitle_json.is_file():
            segments = load_segments_from_json(subtitle_json)
    elif skip_align and subtitle_json.is_file():
        print(f"[pipeline] rebuilding ASS from {subtitle_json}", flush=True)
        segments = load_segments_from_json(subtitle_json)
        _write_ass_for_preset(
            segments,
            subtitle_ass,
            preset,
            clauses_json=subtitle_clauses_json,
            subtitle_fade_ms=subtitle_fade_ms,
            video_layout=video_layout,
        )
    elif subtitle_source == "podcast":
        segments = build_subtitles_from_podcast_timestamps(task_dir, subtitle_srt, subtitle_json)
        _write_ass_for_preset(
            segments,
            subtitle_ass,
            preset,
            clauses_json=subtitle_clauses_json,
            subtitle_fade_ms=subtitle_fade_ms,
            video_layout=video_layout,
        )
    elif subtitle_source == "whisper":
        segments = align_subtitles_with_whisper(
            audio_path,
            alignment_text,
            subtitle_srt,
            subtitle_json,
            model_name=whisper_model,
            language="zh",
            mode=align_mode,
        )
        _write_ass_for_preset(
            segments,
            subtitle_ass,
            preset,
            clauses_json=subtitle_clauses_json,
            subtitle_fade_ms=subtitle_fade_ms,
            video_layout=video_layout,
        )
    else:
        raise ValueError(f"Unknown subtitle_source: {subtitle_source}")

    if segments is None and subtitle_json.is_file():
        segments = load_segments_from_json(subtitle_json)

    progress(40, "layout", "正在编排论文页面与字幕")
    subtitle_ass_path, overlay_ass_path, compose_audio = _resolve_burn_ass_path(
        preset=preset,
        page_width=page_result.width,
        page_height=page_result.height,
        round_segments=segments or [],
        audio_duration=audio_duration,
        work_dir=work_dir,
        subtitle_ass=subtitle_ass,
        skip_key_points=skip_key_points,
        task_dir=task_dir,
        audio_path=audio_path,
        video_layout=video_layout,
    )

    selected_page_paths = page_result.page_paths
    if video_layout is not None and video_layout.visible_page_count > 0:
        selected_page_paths = selected_page_paths[: video_layout.visible_page_count]
    slots = build_equal_page_timeline(
        selected_page_paths,
        audio_duration,
        first_page_weight=2.0,
    )
    write_concat_demuxer(slots, concat_file)
    timeline_json.write_text(
        json.dumps([asdict(slot) for slot in slots], ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    avg_other = audio_duration / (len(slots) + 1) if len(slots) > 1 else audio_duration
    print(
        f"[pipeline] page timing: {len(slots)} pages, "
        f"page1={slots[0].end - slots[0].start:.2f}s (~2x), "
        f"others~{avg_other:.2f}s",
        flush=True,
    )

    page_image_overlays: list[PageImageOverlaySpec] | None = None
    if preset.name == "16:9" and not skip_page_images:
        progress(56, "figures", "正在提取并整理论文图表")
        page_images_json = work_dir / "page_images.json"
        page_images_dir = work_dir / "page_images"
        overlay_clips_dir = work_dir / "page_image_clips"

        extracted = load_document_media(
            task_dir, page_images_dir, page_count=len(page_result.page_paths)
        )
        if extracted:
            print(f"[page_images] using {len(extracted)} shared YOLO media assets", flush=True)
        else:
            print("[page_images] shared media manifest unavailable; continuing without figure overlays", flush=True)
        page_image_events = build_page_image_events(
            extracted,
            slots,
            display_duration=(video_layout.figure_display_duration if video_layout is not None else 3.0),
        )
        write_page_images_json(page_image_events, page_images_json)
        print(
            f"[page_images] extracted {len(extracted)} images -> "
            f"{len(page_image_events)} timed overlays",
            flush=True,
        )

        if page_image_events:
            page_image_overlays = render_page_image_overlays(
                page_image_events,
                overlay_clips_dir,
                canvas_width=preset.width,
                canvas_height=preset.height,
                page_width=page_result.width,
                page_height=page_result.height,
                video_duration=audio_duration,
                video_layout=video_layout,
            )

    progress(74, "encoding", "正在合成画面与音频，这一步通常最久")
    compose_video(
        slots,
        compose_audio,
        subtitle_ass_path,
        output_path,
        width=preset.width,
        height=preset.height,
        background_color=(video_layout.background_color.replace("#", "0x", 1) if video_layout is not None else preset.background_color),
        subtitle_font_size=preset.subtitle_font_size,
        subtitle_fade_ms=subtitle_fade_ms,
        page_image_overlays=page_image_overlays,
        video_layout=video_layout,
        overlay_ass_path=overlay_ass_path,
    )
    if preset.name == "16:9":
        progress(94, "cover", "正在生成横版与社交媒体封面")
        from .auto_surface import generate_task_covers, prepend_cover_to_video

        landscape_cover, _portrait_cover = generate_task_covers(task_dir, work_dir)
        progress(97, "cover", "正在将封面写入视频片头")
        prepend_cover_to_video(output_path, landscape_cover)
    progress(98, "finalizing", "正在封装并校验视频文件")
    print(f"[pipeline] done -> {output_path}", flush=True)
    return output_path
