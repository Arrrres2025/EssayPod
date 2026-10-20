from __future__ import annotations

from pathlib import Path

from .models import OcrBlock

_MIN_CROP_PX = 40


def crop_blocks_from_pdf(
    pdf_path: Path,
    blocks: list[OcrBlock],
    out_dir: Path,
    *,
    dpi: int = 150,
) -> list[OcrBlock]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pip install pymupdf") from exc

    if not blocks:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)
    saved: list[OcrBlock] = []
    type_counters: dict[tuple[int, str], int] = {}

    try:
        for block in blocks:
            if block.page_index < 0 or block.page_index >= doc.page_count:
                continue

            page = doc.load_page(block.page_index)
            x0, y0, x1, y1 = block.bbox_pdf
            clip = fitz.Rect(x0, y0, x1, y1)
            if clip.width < _MIN_CROP_PX or clip.height < _MIN_CROP_PX:
                continue

            counter_key = (block.page_number, block.type)
            type_counters[counter_key] = type_counters.get(counter_key, 0) + 1
            index = type_counters[counter_key]

            safe_type = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in block.type)
            image_path = out_dir / f"page_{block.page_number:04d}_{safe_type}_{index:02d}.png"

            pix = page.get_pixmap(matrix=matrix, clip=clip, alpha=False)
            pix.save(str(image_path))

            saved.append(
                OcrBlock(
                    type=block.type,
                    page_index=block.page_index,
                    page_number=block.page_number,
                    bbox_pdf=block.bbox_pdf,
                    bbox_ocr=block.bbox_ocr,
                    bbox_normalized=block.bbox_normalized,
                    confidence=block.confidence,
                    source=block.source,
                    block_id=block.block_id,
                    image_path=str(image_path),
                    meta=block.meta,
                )
            )
    finally:
        doc.close()

    return saved
