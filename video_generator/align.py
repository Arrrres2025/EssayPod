from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .subtitle_format import format_subtitle_for_ass
from .subtitle_style import (
    build_rounded_box_override,
    build_text_position_override,
    estimate_subtitle_box,
)


@dataclass(frozen=True)
class SubtitleSegment:
    index: int
    start: float
    end: float
    text: str


def _segments_from_stable_result(result) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    for index, segment in enumerate(result.segments, start=1):
        text = (segment.text or "").strip()
        if not text:
            continue
        segments.append(
            SubtitleSegment(
                index=index,
                start=float(segment.start),
                end=float(segment.end),
                text=text,
            )
        )
    return segments


def _segments_from_whisper_result(result: dict) -> list[SubtitleSegment]:
    segments: list[SubtitleSegment] = []
    for index, segment in enumerate(result.get("segments") or [], start=1):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            SubtitleSegment(
                index=index,
                start=float(segment["start"]),
                end=float(segment["end"]),
                text=text,
            )
        )
    return segments


def _format_srt_time(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _format_ass_time(seconds: float) -> str:
    centis = int(round(seconds * 100))
    hours, rem = divmod(centis, 360_000)
    minutes, rem = divmod(rem, 6_000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def _ass_inline_tags(fade_ms: int, *, pos_override: str) -> str:
    tags = [pos_override, r"\q2"]
    if fade_ms > 0:
        tags.append(f"\\fad({fade_ms},{fade_ms})")
    return "{" + "".join(tags) + "}"


def write_ass(
    segments: list[SubtitleSegment],
    path: Path,
    *,
    width: int,
    height: int,
    font_size: int = 22,
    fade_ms: int = 0,
    first_subtitle_fade_ms: int = 0,
    margin_v: int = 28,
    margin_l: int | None = None,
    margin_r: int | None = None,
) -> None:
    if fade_ms > 0:
        fade_ms = max(80, fade_ms)
    if first_subtitle_fade_ms > 0:
        first_subtitle_fade_ms = max(80, first_subtitle_fade_ms)
    side_margin = margin_l if margin_l is not None else max(int(width * 0.06), 32)
    if margin_r is None:
        margin_r = side_margin
    if margin_l is None:
        margin_l = side_margin

    style = (
        f"Style: Default,PingFang SC,{font_size},"
        "&HFFFFFF,&HFFFFFF,&H000000,&H00000000,"
        f"0,0,0,0,100,100,0,0,1,0,0,2,{margin_v},{margin_l},{margin_r},1"
    )
    header = "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 2",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            style,
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
    )
    lines = [header]
    for segment_index, segment in enumerate(segments):
        segment_fade_ms = first_subtitle_fade_ms if segment_index == 0 else fade_ms
        text = format_subtitle_for_ass(
            segment.text,
            canvas_width=width,
            margin_l=margin_l,
            margin_r=margin_r,
            font_size=font_size,
        )
        if not text:
            continue

        box_w, box_h, radius = estimate_subtitle_box(
            text,
            font_size=font_size,
            canvas_width=width,
            margin_l=margin_l,
            margin_r=margin_r,
        )
        box_override = build_rounded_box_override(
            canvas_width=width,
            canvas_height=height,
            margin_v=margin_v,
            box_w=box_w,
            box_h=box_h,
            radius=radius,
            fade_ms=segment_fade_ms,
        )
        pos_override = build_text_position_override(
            canvas_width=width,
            canvas_height=height,
            margin_v=margin_v,
            box_h=box_h,
        )
        start = _format_ass_time(segment.start)
        end = _format_ass_time(segment.end)
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{box_override}")
        lines.append(
            f"Dialogue: 1,{start},{end},Default,,0,0,0,,"
            f"{_ass_inline_tags(segment_fade_ms, pos_override=pos_override)}{text}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_srt(segments: list[SubtitleSegment], path: Path) -> None:
    blocks: list[str] = []
    for segment in segments:
        blocks.append(
            "\n".join(
                [
                    str(segment.index),
                    f"{_format_srt_time(segment.start)} --> {_format_srt_time(segment.end)}",
                    segment.text,
                    "",
                ]
            )
        )
    path.write_text("\n".join(blocks), encoding="utf-8")


def write_subtitle_json(segments: list[SubtitleSegment], path: Path) -> None:
    payload = [asdict(segment) for segment in segments]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _align_with_stable_ts(
    audio_path: Path,
    alignment_text: str,
    *,
    model_name: str,
    language: str,
) -> list[SubtitleSegment]:
    import stable_whisper

    model = stable_whisper.load_model(model_name)
    result = model.align(
        str(audio_path),
        alignment_text,
        language=language,
        regroup=True,
        suppress_silence=True,
    )
    return _segments_from_stable_result(result)


def _align_with_whisper_transcribe(
    audio_path: Path,
    *,
    model_name: str,
    language: str,
) -> list[SubtitleSegment]:
    import whisper

    model = whisper.load_model(model_name)
    result = model.transcribe(
        str(audio_path),
        language=language,
        verbose=False,
    )
    return _segments_from_whisper_result(result)


def align_subtitles_with_whisper(
    audio_path: Path,
    alignment_text: str,
    out_srt: Path,
    out_json: Path,
    *,
    model_name: str = "small",
    language: str = "zh",
    mode: str = "auto",
) -> list[SubtitleSegment]:
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio not found: {audio_path}")
    if not alignment_text.strip():
        raise ValueError("Alignment text is empty")

    segments: list[SubtitleSegment]
    if mode in {"auto", "forced"}:
        try:
            print(f"[align] stable-ts forced alignment (model={model_name})", flush=True)
            segments = _align_with_stable_ts(
                audio_path,
                alignment_text,
                model_name=model_name,
                language=language,
            )
        except ImportError:
            if mode == "forced":
                raise SystemExit(
                    "Missing dependency for forced alignment: pip install stable-ts openai-whisper"
                ) from None
            print("[align] stable-ts not installed; falling back to whisper transcribe", flush=True)
            segments = _align_with_whisper_transcribe(
                audio_path,
                model_name=model_name,
                language=language,
            )
    else:
        print(f"[align] whisper transcribe (model={model_name})", flush=True)
        segments = _align_with_whisper_transcribe(
            audio_path,
            model_name=model_name,
            language=language,
        )

    if not segments:
        raise RuntimeError("Whisper alignment produced no subtitle segments")

    out_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(segments, out_srt)
    write_subtitle_json(segments, out_json)
    print(f"[align] wrote {len(segments)} segments -> {out_srt}", flush=True)
    return segments
