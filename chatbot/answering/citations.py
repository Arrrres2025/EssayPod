from __future__ import annotations

import re
from typing import Any

from chatbot.retrieval.bm25 import tokenize


CITATION_RE = re.compile(r"〔(block_\d{5})〕")


def citation_ids(answer: str) -> list[str]:
    return list(dict.fromkeys(CITATION_RE.findall(answer)))


def _sentences(text: str) -> list[str]:
    return [
        sentence.strip()
        for sentence in re.split(r"(?<=[.!?。！？])\s+", text)
        if sentence.strip()
    ]


def _best_quote(text: str, query: str, answer: str) -> str:
    candidates = _sentences(text)
    if not candidates:
        return text[:240]
    needles = set(tokenize(f"{query} {answer}"))
    ranked = sorted(
        candidates,
        key=lambda sentence: (
            len(needles.intersection(tokenize(sentence))),
            min(len(sentence), 240),
        ),
        reverse=True,
    )
    return ranked[0][:360]


def build_citations(
    answer: str,
    query: str,
    blocks_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for position, block_id in enumerate(citation_ids(answer), start=1):
        block = blocks_by_id.get(block_id)
        if not block:
            continue
        source = str(block.get("source") or block.get("caption_source") or "")
        translation = str(
            block.get("translation")
            or block.get("caption_translation")
            or ""
        )
        citations.append(
            {
                "citation_id": f"citation_{position}",
                "block_id": block_id,
                "quote_source": _best_quote(source, query, answer),
                # Translation is already aligned at block level. Highlighting the
                # complete translated block is more reliable than fuzzy retranslation.
                "quote_translation": translation,
                "heading_path": block.get("heading_path") or [],
                "page_number": block.get("page_number"),
            }
        )
    return citations
