from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pymupdf


RESTORE_VERSION = 1
RESTORABLE_MEDIA_TYPES = {"figure", "table", "isolate_formula"}


def _marker_path(translated_pdf: Path) -> Path:
    return translated_pdf.with_suffix(".media-restored.json")


def _marker_matches(
    marker_path: Path,
    translated_pdf: Path,
    source_pdf: Path,
    manifest_path: Path,
) -> bool:
    if not marker_path.is_file():
        return False
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        target_stat = translated_pdf.stat()
        return (
            marker.get("version") == RESTORE_VERSION
            and marker.get("target_size") == target_stat.st_size
            and marker.get("target_mtime_ns") == target_stat.st_mtime_ns
            and marker.get("source_mtime_ns") == source_pdf.stat().st_mtime_ns
            and marker.get("manifest_mtime_ns") == manifest_path.stat().st_mtime_ns
        )
    except (OSError, ValueError, TypeError):
        return False


def restore_translated_pdf_media(
    source_pdf: str | Path,
    translated_pdf: str | Path,
    manifest_path: str | Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Restore YOLO-cropped media over a translated PDF without rerunning layout.

    PDFMathTranslate rewrites page content streams. Text and fills inside vector
    figures may therefore disappear even when the figure region is excluded from
    translation. The Markdown branch already produced high-resolution crops and
    their source-page bboxes, so those pixels can be placed back exactly once.
    """

    source_path = Path(source_pdf)
    target_path = Path(translated_pdf)
    manifest = Path(manifest_path)
    marker = _marker_path(target_path)
    if not source_path.is_file() or not target_path.is_file() or not manifest.is_file():
        return {"restored": False, "count": 0, "reason": "missing-input"}
    if not force and _marker_matches(marker, target_path, source_path, manifest):
        return {"restored": False, "count": 0, "reason": "already-restored"}

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    entries = [
        item
        for item in payload.get("media") or []
        if item.get("type") in RESTORABLE_MEDIA_TYPES
        and isinstance(item.get("bbox_pdf"), list)
        and len(item["bbox_pdf"]) == 4
        and item.get("asset_path")
    ]
    if not entries:
        return {"restored": False, "count": 0, "reason": "no-media"}

    source_doc = pymupdf.open(source_path)
    target_doc = pymupdf.open(target_path)
    restored = 0
    try:
        for item in entries:
            page_index = int(item.get("page_index", -1))
            if page_index < 0 or page_index >= len(source_doc) or page_index >= len(target_doc):
                continue
            asset_path = (manifest.parent / str(item["asset_path"])).resolve()
            assets_root = (manifest.parent / "assets").resolve()
            if assets_root not in asset_path.parents or not asset_path.is_file():
                continue

            source_rect = source_doc[page_index].rect
            target_page = target_doc[page_index]
            target_rect = target_page.rect
            if source_rect.width <= 0 or source_rect.height <= 0:
                continue
            sx = target_rect.width / source_rect.width
            sy = target_rect.height / source_rect.height
            x0, y0, x1, y1 = (float(value) for value in item["bbox_pdf"])
            # Markdown crops use a one-point padding. Restore to the same padded
            # rectangle so the crop is not squeezed and edge labels stay intact.
            x0 = max(source_rect.x0, x0 - 1.0)
            y0 = max(source_rect.y0, y0 - 1.0)
            x1 = min(source_rect.x1, x1 + 1.0)
            y1 = min(source_rect.y1, y1 + 1.0)
            rect = pymupdf.Rect(
                target_rect.x0 + (x0 - source_rect.x0) * sx,
                target_rect.y0 + (y0 - source_rect.y0) * sy,
                target_rect.x0 + (x1 - source_rect.x0) * sx,
                target_rect.y0 + (y1 - source_rect.y0) * sy,
            )
            if rect.is_empty or rect.is_infinite:
                continue
            target_page.insert_image(
                rect,
                filename=str(asset_path),
                keep_proportion=False,
                overlay=True,
            )
            restored += 1

        if restored:
            temporary = target_path.with_name(f".{target_path.stem}.media-restoring.pdf")
            target_doc.save(temporary, garbage=3, deflate=True)
            target_doc.close()
            target_doc = None
            os.replace(temporary, target_path)
            stat = target_path.stat()
            marker.write_text(
                json.dumps(
                    {
                        "version": RESTORE_VERSION,
                        "count": restored,
                        "target_size": stat.st_size,
                        "target_mtime_ns": stat.st_mtime_ns,
                        "source_mtime_ns": source_path.stat().st_mtime_ns,
                        "manifest_mtime_ns": manifest.stat().st_mtime_ns,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        source_doc.close()
        if target_doc is not None:
            target_doc.close()

    return {"restored": bool(restored), "count": restored, "reason": "ok"}

