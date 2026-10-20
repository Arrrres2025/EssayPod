from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

_FIGURE_CAPTION_RE = re.compile(
    r"(?i)^(?:Figure|Fig\.)\s*\d+(?:\s*[:：.-]|\s*$)"
)
_MIN_REGION_PX = 80


@dataclass(frozen=True)
class ExtractedPageImage:
    page_index: int
    image_path: Path
    source: str
    caption: str | None = None
    bbox_pdf: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class PageImageEvent:
    page_index: int
    image_path: Path
    appear_at: float
    disappear_at: float
    caption: str | None = None
    native_width: int = 0
    native_height: int = 0


def _block_text(block: dict) -> str:
    parts: list[str] = []
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            parts.append(span.get("text", ""))
    return "".join(parts).strip()


def _union_rects(rects: list) -> object | None:
    if not rects:
        return None
    union = rects[0]
    for rect in rects[1:]:
        union |= rect
    return union


def _detect_caption_regions(page) -> list[tuple[str, object]]:
    captions: list[tuple[str, object]] = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") != 0:
            continue
        text = _block_text(block)
        if not text or not _FIGURE_CAPTION_RE.match(text):
            continue
        captions.append((text.split("\n", 1)[0][:120], page.rect.__class__(block["bbox"])))
    captions.sort(key=lambda item: item[1].y0)
    return captions


def _figure_rect_above_caption(
    page,
    caption_rect,
    *,
    top_margin: float = 36.0,
    bottom_limit: float | None = None,
) -> object | None:
    import fitz

    page_rect = page.rect
    cap_top = caption_rect.y0 - 6.0
    region_top = max(top_margin, bottom_limit if bottom_limit is not None else top_margin)
    if cap_top <= region_top:
        return None

    drawings = page.get_drawings()
    drawing_rects = [
        fitz.Rect(item["rect"])
        for item in drawings
        if item["rect"].y1 <= cap_top and item["rect"].y0 >= region_top and item["rect"].height > 4
    ]
    if drawing_rects:
        union = _union_rects(drawing_rects)
        if union is not None:
            union = fitz.Rect(
                max(union.x0, 48.0),
                max(union.y0, region_top),
                min(union.x1, page_rect.width - 48.0),
                min(union.y1, cap_top),
            )
            if union.width >= _MIN_REGION_PX and union.height >= _MIN_REGION_PX:
                return union

    fallback = fitz.Rect(48.0, region_top, page_rect.width - 48.0, cap_top)
    if fallback.width >= _MIN_REGION_PX and fallback.height >= _MIN_REGION_PX:
        return fallback
    return None


def _save_pixmap(pix, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pix.alpha:
        pix.save(str(path))
    else:
        pix.save(str(path))


def _extract_embedded_images(page, page_index: int, out_dir: Path) -> list[ExtractedPageImage]:
    results: list[ExtractedPageImage] = []
    seen_xrefs: set[int] = set()

    for img_index, img in enumerate(page.get_images(full=True)):
        xref = int(img[0])
        if xref in seen_xrefs:
            continue
        seen_xrefs.add(xref)

        rects = page.get_image_rects(xref)
        if not rects:
            continue
        rect = sorted(rects, key=lambda item: (item.y0, item.x0))[0]
        if rect.width < _MIN_REGION_PX or rect.height < _MIN_REGION_PX:
            continue

        base = page.parent.extract_image(xref)
        ext = str(base.get("ext") or "png").lower()
        if ext not in {"png", "jpg", "jpeg", "webp"}:
            ext = "png"
        image_path = out_dir / f"page_{page_index + 1:04d}_embedded_{img_index + 1:02d}.{ext}"
        image_path.write_bytes(base["image"])
        results.append(
            ExtractedPageImage(
                page_index=page_index,
                image_path=image_path,
                source="embedded",
                bbox_pdf=(rect.x0, rect.y0, rect.x1, rect.y1),
            )
        )

    # Sort embedded images top-to-bottom using bbox when available
    results.sort(key=lambda item: (item.bbox_pdf[1] if item.bbox_pdf else 0.0, item.page_index))
    return results


def _extract_caption_clips(page, page_index: int, out_dir: Path, *, dpi: int) -> list[ExtractedPageImage]:
    import fitz

    results: list[ExtractedPageImage] = []
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    captions = _detect_caption_regions(page)
    for fig_index, (caption, caption_rect) in enumerate(captions):
        bottom_limit = None
        if fig_index > 0:
            bottom_limit = captions[fig_index - 1][1].y1 + 4.0
        fig_rect = _figure_rect_above_caption(page, caption_rect, bottom_limit=bottom_limit)
        if fig_rect is None:
            continue
        pix = page.get_pixmap(matrix=matrix, clip=fig_rect, alpha=False)
        image_path = out_dir / f"page_{page_index + 1:04d}_clip_{fig_index + 1:02d}.png"
        _save_pixmap(pix, image_path)
        results.append(
            ExtractedPageImage(
                page_index=page_index,
                image_path=image_path,
                source="clip",
                caption=caption,
                bbox_pdf=(fig_rect.x0, fig_rect.y0, fig_rect.x1, fig_rect.y1),
            )
        )
    return results


def extract_pdf_page_images(
    pdf_path: Path,
    out_dir: Path,
    *,
    page_count: int,
    dpi: int = 150,
) -> list[ExtractedPageImage]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise SystemExit("Missing dependency: pip install pymupdf") from exc

    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    extracted: list[ExtractedPageImage] = []

    try:
        total = min(page_count, doc.page_count)
        for page_index in range(total):
            page = doc.load_page(page_index)
            embedded = _extract_embedded_images(page, page_index, out_dir)
            if embedded:
                extracted.extend(embedded)
                continue
            extracted.extend(_extract_caption_clips(page, page_index, out_dir, dpi=dpi))
    finally:
        doc.close()

    return extracted


def load_document_media(task_dir: Path, out_dir: Path, *, page_count: int) -> list[ExtractedPageImage]:
    """Materialize shared YOLO assets, with legacy OCR read compatibility."""
    source_json = task_dir / "translation" / "media_manifest.json"
    legacy = False
    if not source_json.is_file():
        source_json = task_dir / "ocr_block" / "ocr_blocks.json"
        legacy = True
    if not source_json.is_file():
        return []
    try:
        payload = json.loads(source_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []

    out_dir.mkdir(parents=True, exist_ok=True)
    loaded: list[ExtractedPageImage] = []
    items = payload.get("images") if legacy else payload.get("media")
    for index, item in enumerate(items or [], start=1):
        page_index = int(item.get("page_index", -1))
        if page_index < 0 or page_index >= page_count:
            continue
        source_path = Path(str(item.get("asset_path") or item.get("image_path") or ""))
        if not source_path.is_absolute():
            source_path = (task_dir / "ocr_block" / "images" / source_path.name) if legacy else (task_dir / "translation" / source_path)
        if not source_path.is_file():
            continue
        extension = source_path.suffix.lower() or ".png"
        destination = out_dir / f"ocr_page_{page_index + 1:04d}_{index:02d}{extension}"
        destination.write_bytes(source_path.read_bytes())
        raw_bbox = item.get("bbox_pdf")
        bbox = tuple(float(v) for v in raw_bbox) if isinstance(raw_bbox, list) and len(raw_bbox) == 4 else None
        meta = item.get("meta") or {}
        texts = meta.get("texts") or []
        caption = str(item.get("caption") or "").strip() or " ".join(str(value) for value in texts if value).strip() or None
        loaded.append(
            ExtractedPageImage(
                page_index=page_index,
                image_path=destination,
                source=str(item.get("source") or ("legacy_ocr" if legacy else "yolo_layout")),
                caption=caption,
                bbox_pdf=bbox,
            )
        )
    return loaded


def build_page_image_events(
    extracted: list[ExtractedPageImage],
    slots: list,
    *,
    display_duration: float = 3.0,
) -> list[PageImageEvent]:
    if not extracted or not slots:
        return []

    slot_by_page = {slot.page_index: slot for slot in slots}
    events: list[PageImageEvent] = []

    items_by_page: dict[int, list[ExtractedPageImage]] = {}
    for item in extracted:
        if item.page_index in slot_by_page:
            items_by_page.setdefault(item.page_index, []).append(item)

    for page_index, page_items in items_by_page.items():
        slot = slot_by_page[page_index]
        page_duration = max(float(slot.end - slot.start), 0.05)
        active_start = float(slot.start) + page_duration * 0.25
        active_end = float(slot.end)
        cursor = active_start
        for item in page_items:
            if cursor >= active_end - 0.05:
                break
            appear_at = cursor
            disappear_at = min(active_end, appear_at + max(display_duration, 0.05))
            cursor = disappear_at
            native_w, native_h = _probe_image_size(item.image_path)
            events.append(
                PageImageEvent(
                    page_index=item.page_index,
                    image_path=item.image_path,
                    appear_at=appear_at,
                    disappear_at=disappear_at,
                    caption=item.caption,
                    native_width=native_w,
                    native_height=native_h,
                )
            )

    events.sort(key=lambda event: (event.appear_at, event.page_index))
    return events


def _probe_image_size(path: Path) -> tuple[int, int]:
    try:
        from PIL import Image
    except ImportError:
        return (0, 0)
    with Image.open(path) as image:
        return image.size


def write_page_images_json(events: list[PageImageEvent], path: Path) -> None:
    payload = [
        {
            **asdict(event),
            "image_path": str(event.image_path),
        }
        for event in events
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_page_images_json(path: Path) -> list[PageImageEvent]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [
        PageImageEvent(
            page_index=int(item["page_index"]),
            image_path=Path(item["image_path"]),
            appear_at=float(item["appear_at"]),
            disappear_at=float(item.get("disappear_at") or item["appear_at"] + 3.0),
            caption=item.get("caption"),
            native_width=int(item.get("native_width") or 0),
            native_height=int(item.get("native_height") or 0),
        )
        for item in payload
    ]
