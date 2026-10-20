#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from .pipeline import generate_video


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate paper video: PDF page flip + podcast/Whisper subtitles + audio.",
    )
    parser.add_argument(
        "--task-dir",
        type=Path,
        required=True,
        help="EssayPod task directory containing input.pdf, podcast.mp3, transcript.txt",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output mp4 path (default: <task-dir>/video/paper_video.mp4)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Intermediate artifacts directory (default: <task-dir>/video)",
    )
    parser.add_argument(
        "--format",
        dest="video_format",
        choices=["16:9", "9:16"],
        default="16:9",
        help="Output canvas: 16:9=1920x1080 landscape, 9:16=1080x1920 portrait (pages fit inside)",
    )
    parser.add_argument(
        "--whisper-model",
        default="small",
        help="Whisper model for forced alignment (tiny/base/small/medium/large-v3)",
    )
    parser.add_argument(
        "--pdf-dpi",
        type=int,
        default=200,
        help="PDF render DPI (higher = sharper when scaled to 1080p canvas)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Only render first N PDF pages (useful for quick tests)",
    )
    parser.add_argument(
        "--skip-align",
        action="store_true",
        help="Reuse existing subtitles.srt in work-dir if present",
    )
    parser.add_argument(
        "--subtitle-source",
        choices=["podcast", "whisper"],
        default="podcast",
        help="podcast=round timestamps from run.log; whisper=local ASR alignment",
    )
    parser.add_argument(
        "--subtitle-fade-ms",
        type=int,
        default=0,
        help="Subtitle fade in/out duration in ms (0=off, e.g. 280 for soft fade)",
    )
    parser.add_argument(
        "--align-mode",
        choices=["auto", "forced", "transcribe"],
        default="auto",
        help="auto=stable-ts forced align if available else whisper transcribe",
    )
    parser.add_argument(
        "--skip-key-points",
        action="store_true",
        help="Skip key-points agent (16:9 only); reuse key_points.json if present",
    )
    parser.add_argument(
        "--skip-page-images",
        action="store_true",
        help="Skip PDF page image extraction and right-side overlays (16:9 only)",
    )
    args = parser.parse_args()

    generate_video(
        args.task_dir,
        output_path=args.output,
        work_dir=args.work_dir,
        video_format=args.video_format,
        whisper_model=args.whisper_model,
        pdf_dpi=args.pdf_dpi,
        max_pages=args.max_pages,
        skip_align=args.skip_align,
        align_mode=args.align_mode,
        subtitle_source=args.subtitle_source,
        subtitle_fade_ms=args.subtitle_fade_ms,
        skip_key_points=args.skip_key_points,
        skip_page_images=args.skip_page_images,
    )


if __name__ == "__main__":
    main()
