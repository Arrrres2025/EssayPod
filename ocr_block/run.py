from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import run_ocr_for_task, run_ocr_image_extraction


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Extract image blocks from a paper PDF via remote Paddle Layout OCR.",
    )
    parser.add_argument("--pdf", type=Path, help="Path to input PDF")
    parser.add_argument("--task-dir", type=Path, help="EssayPod task directory containing input.pdf")
    parser.add_argument("--out-dir", type=Path, help="Output directory (default: <task-dir>/ocr_block or ./ocr_block_out)")
    parser.add_argument("--dpi", type=int, default=150, help="DPI used when cropping image regions from PDF")
    parser.add_argument("--mode", choices=["fast", "accurate"], default="accurate")
    parser.add_argument("--device", choices=["gpu", "cpu"], default="gpu")
    args = parser.parse_args(argv)

    if args.task_dir:
        payload = run_ocr_for_task(
            args.task_dir,
            dpi=args.dpi,
            mode=args.mode,
            device=args.device,
        )
    else:
        if not args.pdf:
            parser.error("Provide --pdf or --task-dir")
        out_dir = args.out_dir or Path("ocr_block_out")
        payload = run_ocr_image_extraction(
            pdf_path=args.pdf,
            out_dir=out_dir,
            dpi=args.dpi,
            mode=args.mode,
            device=args.device,
        )

    print(json.dumps(payload["timing"], ensure_ascii=False, indent=2))
    print(f"Saved {len(payload['images'])} images", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
