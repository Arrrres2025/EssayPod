from __future__ import annotations

from typing import Any

from .models import OcrBlock

# Paddle layout types treated as figures/images for cropping.
DEFAULT_IMAGE_TYPES = frozenset(
    {
        "figure",
        "image",
        "picture",
        "photo",
        "chart",
        "diagram",
        "graph",
        "illustration",
    }
)


def _parse_bbox(raw: Any) -> tuple[float, float, float, float] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        keys = ("x1", "y1", "x2", "y2")
        if all(key in raw for key in keys):
            x1, y1, x2, y2 = (float(raw[k]) for k in keys)
        elif all(key in raw for key in ("x", "y", "w", "h")):
            x1 = float(raw["x"])
            y1 = float(raw["y"])
            x2 = x1 + float(raw["w"])
            y2 = y1 + float(raw["h"])
        else:
            return None
    elif isinstance(raw, (list, tuple)) and len(raw) == 4:
        x1, y1, x2, y2 = (float(v) for v in raw)
    else:
        return None
    if x2 <= x1 or y2 <= y1:
        return None
    return (x1, y1, x2, y2)


def _page_scale(page: dict[str, Any]) -> float:
    coord = page.get("coordinate_system") or {}
    scale = coord.get("scale_from_pdf_points")
    if scale is None:
        return 1.0
    try:
        value = float(scale)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def ocr_bbox_to_pdf_bbox(
    bbox_ocr: tuple[float, float, float, float],
    *,
    scale_from_pdf_points: float,
) -> tuple[float, float, float, float]:
    """Convert OCR pixel bbox (top-left origin) to PDF point bbox for PyMuPDF."""
    scale = scale_from_pdf_points if scale_from_pdf_points > 0 else 1.0
    x1, y1, x2, y2 = bbox_ocr
    return (x1 / scale, y1 / scale, x2 / scale, y2 / scale)


def extract_layout_blocks(
    ocr_result: dict[str, Any],
    *,
    image_types: frozenset[str] | None = None,
) -> tuple[list[OcrBlock], dict[str, list[dict[str, Any]]]]:
    """Parse remote OCR JSON into OcrBlock list and blocks grouped by type."""
    image_types = image_types or DEFAULT_IMAGE_TYPES
    all_blocks: list[OcrBlock] = []
    by_type: dict[str, list[dict[str, Any]]] = {}

    for page in ocr_result.get("pages") or []:
        page_number = int(page.get("page_number") or 1)
        page_index = max(page_number - 1, 0)
        scale = _page_scale(page)

        for block_index, block in enumerate(page.get("layout_blocks") or [], start=1):
            block_type = str(block.get("type") or "unknown").strip().lower()
            bbox_ocr = _parse_bbox(block.get("bbox"))
            if bbox_ocr is None:
                continue

            bbox_pdf = ocr_bbox_to_pdf_bbox(bbox_ocr, scale_from_pdf_points=scale)
            score = block.get("score")
            confidence = float(score) if score is not None else None
            block_id = str(block.get("id") or f"p{page_number:04d}_{block_type}_{block_index:02d}")

            record = {
                "type": block_type,
                "page_index": page_index,
                "page_number": page_number,
                "bbox_ocr": list(bbox_ocr),
                "bbox_pdf": list(bbox_pdf),
                "confidence": confidence,
                "block_id": block_id,
                "texts": block.get("texts") or [],
                "scale_from_pdf_points": scale,
            }
            by_type.setdefault(block_type, []).append(record)

            if block_type not in image_types:
                continue

            all_blocks.append(
                OcrBlock(
                    type=block_type,
                    page_index=page_index,
                    page_number=page_number,
                    bbox_pdf=bbox_pdf,
                    bbox_ocr=bbox_ocr,
                    confidence=confidence,
                    source="remote_layout_ocr",
                    block_id=block_id,
                    meta={
                        "scale_from_pdf_points": scale,
                        "texts": block.get("texts") or [],
                    },
                )
            )

    all_blocks.sort(key=lambda item: (item.page_index, item.bbox_pdf[1], item.bbox_pdf[0]))
    return all_blocks, by_type
