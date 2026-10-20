from __future__ import annotations

import html
import json
import re
from pathlib import Path


class ExtractionError(ValueError):
    pass


TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".rst", ".csv", ".json", ".html", ".htm"}


def extract_text(path: Path, max_chars: int) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _extract_pdf(path)
    elif suffix in TEXT_SUFFIXES:
        text = path.read_text(encoding="utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        elif suffix == ".json":
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except json.JSONDecodeError:
                pass
    else:
        raise ExtractionError(
            f"Unsupported file type {suffix or '(none)'}. Use PDF, Markdown, TXT, "
            "HTML, CSV, RST, or JSON."
        )
    text = re.sub(r"\r\n?", "\n", text).strip()
    if not text:
        raise ExtractionError("No readable text was extracted from the source.")
    return text[:max_chars]


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ExtractionError(
            "PDF support requires pypdf. Run: python -m pip install -e ."
        ) from exc
    try:
        reader = PdfReader(str(path))
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            pages.append(f"\n\n--- Page {number} ---\n\n{page.extract_text() or ''}")
        return "".join(pages)
    except Exception as exc:
        raise ExtractionError(f"Could not read PDF: {exc}") from exc

