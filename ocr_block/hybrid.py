from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import OcrBlock

_FIGURE_CAPTION_RE = re.compile(r"(?i)^(?:figure|fig\.)\s*(\d+)")


@dataclass(frozen=True)
class HybridPdfPlan:
    direct_images: list[OcrBlock]
    ocr_page_indices: list[int]
    figure_hints: dict[int, list[dict[str, str]]]


def _block_text(block: dict) -> str:
    return " ".join(
        span.get("text", "").strip()
        for line in block.get("lines", [])
        for span in line.get("spans", [])
        if span.get("text", "").strip()
    ).strip()


def _figure_captions(page) -> list[dict[str, str]]:
    captions: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        text = _block_text(block)
        match = _FIGURE_CAPTION_RE.match(text)
        if match:
            figure_id = f"figure_{int(match.group(1))}"
            if figure_id in seen_ids:
                continue
            seen_ids.add(figure_id)
            captions.append(
                {
                    "figure_id": figure_id,
                    "label": f"Figure {int(match.group(1))}",
                    "caption": text,
                }
            )
    return captions


def _is_standalone_bitmap(rect, page_rect) -> bool:
    width_ratio = rect.width / max(page_rect.width, 1)
    height_ratio = rect.height / max(page_rect.height, 1)
    area_ratio = rect.width * rect.height / max(page_rect.width * page_rect.height, 1)
    return width_ratio >= 0.35 and height_ratio >= 0.12 and area_ratio >= 0.06


def plan_hybrid_pdf_extraction(pdf_path: Path) -> HybridPdfPlan:
    """Route standalone bitmaps directly and composite figure pages to OCR."""
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pip install pymupdf") from exc

    doc = fitz.open(str(pdf_path))
    direct: list[OcrBlock] = []
    ocr_pages: list[int] = []
    figure_hints: dict[int, list[dict[str, str]]] = {}
    try:
        for page_index in range(doc.page_count):
            page = doc.load_page(page_index)
            captions = _figure_captions(page)
            if captions:
                figure_hints[page_index] = captions
            page_direct: list[OcrBlock] = []
            seen: set[tuple[int, int, int, int, int]] = set()
            for image in page.get_images(full=True):
                xref = int(image[0])
                for rect in page.get_image_rects(xref):
                    if not _is_standalone_bitmap(rect, page.rect):
                        continue
                    key = (
                        xref,
                        round(rect.x0),
                        round(rect.y0),
                        round(rect.x1),
                        round(rect.y1),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    page_direct.append(
                        OcrBlock(
                            type="image",
                            page_index=page_index,
                            page_number=page_index + 1,
                            bbox_pdf=(rect.x0, rect.y0, rect.x1, rect.y1),
                            source="pdf_embedded",
                            block_id=f"p{page_index + 1:04d}_embedded_{len(page_direct) + 1:02d}",
                            meta={"xref": xref, **(captions[0] if captions else {})},
                        )
                    )

            if page_direct:
                direct.extend(page_direct)
            elif captions:
                # No complete raster object: the figure is likely composed of
                # vectors, labels and/or small bitmap fragments.
                ocr_pages.append(page_index)
    finally:
        doc.close()
    return HybridPdfPlan(
        direct_images=direct,
        ocr_page_indices=ocr_pages,
        figure_hints=figure_hints,
    )


def _raw_bitmap_is_usable(path: Path) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return True
    try:
        with Image.open(path) as image:
            sample = image.convert("RGB").resize((64, 64))
            pixels = list(sample.getdata())
    except Exception:
        return False
    black_ratio = sum(1 for pixel in pixels if max(pixel) < 8) / max(len(pixels), 1)
    return black_ratio < 0.9


def extract_direct_bitmaps(
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

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    saved: list[OcrBlock] = []
    try:
        for block in blocks:
            xref = int(block.meta["xref"])
            payload = doc.extract_image(xref)
            extension = str(payload.get("ext") or "png").lower()
            if extension not in {"png", "jpg", "jpeg", "webp"}:
                extension = "png"
            path = out_dir / f"{block.block_id}.{extension}"
            path.write_bytes(payload["image"])
            source = block.source
            if not _raw_bitmap_is_usable(path):
                # Some image XObjects require a PDF mask or indexed color
                # space. Their raw stream can otherwise appear as a black box.
                page = doc.load_page(block.page_index)
                clip = fitz.Rect(*block.bbox_pdf)
                rendered_path = out_dir / f"{block.block_id}_rendered.png"
                page.get_pixmap(
                    matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0),
                    clip=clip,
                    alpha=False,
                ).save(str(rendered_path))
                path.unlink(missing_ok=True)
                path = rendered_path
                source = "pdf_embedded_bbox_render"
            saved.append(
                OcrBlock(
                    **{
                        **block.__dict__,
                        "source": source,
                        "image_path": str(path),
                    }
                )
            )
    finally:
        doc.close()
    return saved


def write_page_subset(pdf_path: Path, page_indices: list[int], output_path: Path) -> None:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pip install pymupdf") from exc

    output_path.parent.mkdir(parents=True, exist_ok=True)
    source = fitz.open(str(pdf_path))
    subset = fitz.open()
    try:
        for page_index in page_indices:
            subset.insert_pdf(source, from_page=page_index, to_page=page_index)
        subset.save(str(output_path), garbage=4, deflate=True)
    finally:
        subset.close()
        source.close()
