from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_REFERENCES_HEADING_RE = re.compile(r"(?i)^references?$|^参考文献$")


@dataclass(frozen=True)
class PageRenderResult:
    page_paths: list[Path]
    width: int
    height: int
    dpi: int
    references_page: int | None = None


def _references_page_index_in_doc(doc) -> int | None:
    for index in range(doc.page_count):
        text = doc.load_page(index).get_text("text")
        for line in text.splitlines():
            if _REFERENCES_HEADING_RE.match(line.strip()):
                return index
    return None


def find_references_page_index(pdf_path: Path) -> int | None:
    """Return zero-based page index whose text contains a References section heading."""
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise SystemExit("Missing dependency: pip install pymupdf") from exc

    doc = fitz.open(str(pdf_path))
    try:
        return _references_page_index_in_doc(doc)
    finally:
        doc.close()


def render_pdf_pages(
    pdf_path: Path,
    out_dir: Path,
    *,
    dpi: int = 150,
    max_pages: int | None = None,
    stop_at_references: bool = True,
) -> PageRenderResult:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise SystemExit("Missing dependency: pip install pymupdf") from exc

    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(str(pdf_path))
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    references_page: int | None = None
    page_count = doc.page_count
    total = page_count
    if stop_at_references and max_pages is None:
        references_page = _references_page_index_in_doc(doc)
        if references_page is not None:
            total = references_page + 1
    if max_pages is not None:
        total = min(total, max_pages)

    page_paths: list[Path] = []
    width = 0
    height = 0

    for index in range(total):
        page = doc.load_page(index)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        if index == 0:
            width, height = pix.width, pix.height
        page_path = out_dir / f"page_{index + 1:04d}.png"
        pix.save(str(page_path))
        page_paths.append(page_path)

    doc.close()
    if not page_paths:
        raise ValueError(f"No pages rendered from {pdf_path}")

    if references_page is not None:
        print(
            f"[pdf] stopping at References page {references_page + 1}/{page_count} "
            f"-> {len(page_paths)} pages rendered",
            flush=True,
        )

    return PageRenderResult(
        page_paths=page_paths,
        width=width,
        height=height,
        dpi=dpi,
        references_page=references_page,
    )


def probe_pdf_page_pixel_size(pdf_path: Path, *, dpi: int = 200) -> tuple[int, int]:
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise SystemExit("Missing dependency: pip install pymupdf") from exc

    doc = fitz.open(str(pdf_path))
    try:
        page = doc.load_page(0)
        zoom = dpi / 72.0
        width = int(round(page.rect.width * zoom))
        height = int(round(page.rect.height * zoom))
        return width, height
    finally:
        doc.close()
