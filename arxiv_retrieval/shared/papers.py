from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import feedparser


_ARXIV_ID_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)",
    re.I,
)


def normalize_arxiv_id(value: str) -> str:
    text = (value or "").strip()
    match = _ARXIV_ID_RE.search(text)
    if not match:
        return text.rstrip(".pdf")
    return re.sub(r"v\d+$", "", match.group(1))


def _parse_datetime(value: Any) -> str | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    try:
        return datetime(*value[:6]).isoformat()
    except Exception:
        return str(value)


def entry_to_paper(entry: Any, source: str = "") -> dict[str, Any]:
    link = getattr(entry, "link", "") or ""
    arxiv_id = normalize_arxiv_id(getattr(entry, "id", "") or link)
    pdf_url = ""
    abs_url = link
    for l in getattr(entry, "links", []) or []:
        href = l.get("href", "")
        rel = l.get("rel", "")
        title = (l.get("title") or "").lower()
        if title == "pdf" or href.endswith(".pdf"):
            pdf_url = href
        if rel == "alternate" and "abs" in href:
            abs_url = href
    if not pdf_url and arxiv_id:
        pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
    if not abs_url and arxiv_id:
        abs_url = f"https://arxiv.org/abs/{arxiv_id}"

    categories = []
    for tag in getattr(entry, "tags", []) or []:
        term = tag.get("term")
        if term:
            categories.append(term)

    authors = []
    for a in getattr(entry, "authors", []) or []:
        name = a.get("name") if isinstance(a, dict) else getattr(a, "name", None)
        if name:
            authors.append(name)

    summary = getattr(entry, "summary", "") or getattr(entry, "description", "") or ""
    summary = re.sub(r"<[^>]+>", " ", summary)
    summary = re.sub(r"\s+", " ", summary).strip()

    published = _parse_datetime(getattr(entry, "published_parsed", None)) or getattr(
        entry, "published", None
    )
    updated = _parse_datetime(getattr(entry, "updated_parsed", None)) or getattr(
        entry, "updated", None
    )

    return {
        "id": arxiv_id,
        "title": re.sub(r"\s+", " ", (getattr(entry, "title", "") or "").strip()),
        "authors": authors,
        "summary": summary,
        "categories": categories,
        "published": published,
        "updated": updated,
        "abs_url": abs_url,
        "pdf_url": pdf_url,
        "source": source,
    }


def parse_feed(content: bytes | str, source: str = "") -> list[dict[str, Any]]:
    feed = feedparser.parse(content)
    papers = [entry_to_paper(e, source=source) for e in feed.entries]
    return [p for p in papers if p.get("id") or p.get("title")]


def dedupe_papers(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for paper in papers:
        key = paper.get("id") or paper.get("abs_url") or paper.get("title")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(paper)
    return out


def sort_papers_by_date(papers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(p: dict[str, Any]) -> str:
        return p.get("published") or p.get("updated") or ""

    return sorted(papers, key=key, reverse=True)
