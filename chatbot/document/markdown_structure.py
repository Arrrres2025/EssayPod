from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)\s*$")
LIST_RE = re.compile(r"^(?:[-+*•]\s+|\d+[.)]\s+)")


@dataclass
class MarkdownBlock:
    block_id: str
    type: str
    source: str
    translation: str = ""
    heading_level: int = 0
    heading_path: list[str] = field(default_factory=list)
    asset_path: str = ""
    media_id: str = ""
    media_type: str = ""
    caption_source: str = ""
    caption_translation: str = ""
    page_number: int | None = None
    bbox_pdf: list[float] = field(default_factory=list)


def _stable_id(index: int, block_type: str, path: list[str]) -> str:
    # Short IDs are deliberately used in prompts: long hash suffixes are often
    # abbreviated by LLMs, which breaks deterministic citation resolution.
    return f"block_{index:05d}"


def _parse_markdown(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.replace("\r\n", "\n").split("\n")
    parsed: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        heading = HEADING_RE.match(line)
        if heading:
            parsed.append(
                {
                    "type": "heading",
                    "text": heading.group(2).strip(),
                    "level": len(heading.group(1)),
                }
            )
            index += 1
            continue
        image = IMAGE_RE.match(line)
        if image:
            parsed.append(
                {
                    "type": "media",
                    "text": "",
                    "alt": image.group(1).strip(),
                    "asset_path": image.group(2).strip().removeprefix("./"),
                }
            )
            index += 1
            continue
        if line.startswith("*") and line.endswith("*") and len(line) > 2:
            parsed.append({"type": "caption", "text": line[1:-1].strip()})
            index += 1
            continue
        if LIST_RE.match(line):
            items: list[str] = []
            while index < len(lines):
                candidate = lines[index].strip()
                if not LIST_RE.match(candidate):
                    break
                items.append(LIST_RE.sub("", candidate, count=1).strip())
                index += 1
            parsed.append({"type": "list", "text": "\n".join(items)})
            continue
        paragraph = [line]
        index += 1
        while index < len(lines):
            candidate = lines[index].strip()
            if not candidate:
                break
            if HEADING_RE.match(candidate) or IMAGE_RE.match(candidate):
                break
            if candidate.startswith("*") and candidate.endswith("*"):
                break
            if LIST_RE.match(candidate):
                break
            paragraph.append(candidate)
            index += 1
        parsed.append({"type": "paragraph", "text": " ".join(paragraph)})
    return parsed


def _load_media(manifest_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    if not manifest_path.is_file():
        return {}, {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    by_path: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}
    for item in payload.get("media", []):
        path = str(item.get("asset_path") or item.get("image_path") or "").removeprefix("./")
        if path:
            by_path[path] = item
        media_id = str(item.get("media_id") or item.get("figure_id") or "")
        if media_id:
            by_id[media_id] = item
    return by_path, by_id


def build_bilingual_structure(
    source_path: Path,
    target_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    source_items = _parse_markdown(source_path.read_text(encoding="utf-8", errors="replace"))
    target_items = _parse_markdown(
        target_path.read_text(encoding="utf-8", errors="replace")
        if target_path.is_file()
        else ""
    )
    media_by_path, _ = _load_media(manifest_path)
    heading_path: list[str] = []
    blocks: list[MarkdownBlock] = []
    target_cursor = 0

    for source_index, item in enumerate(source_items):
        target: dict[str, Any] = {}
        if target_cursor < len(target_items):
            # Translation preserves the Markdown shell. Search a short window to
            # tolerate an omitted/duplicated paragraph without shifting the paper.
            for candidate_index in range(target_cursor, min(len(target_items), target_cursor + 4)):
                candidate = target_items[candidate_index]
                same_media = (
                    item["type"] == "media"
                    and candidate["type"] == "media"
                    and candidate.get("asset_path") == item.get("asset_path")
                )
                if candidate["type"] == item["type"] and (
                    item["type"] != "media" or same_media
                ):
                    target = candidate
                    target_cursor = candidate_index + 1
                    break

        if item["type"] == "heading":
            level = int(item["level"])
            heading_path = heading_path[: max(0, level - 1)]
            while len(heading_path) < level - 1:
                heading_path.append("")
            heading_path.append(item["text"])
        block_id = _stable_id(source_index, item["type"], heading_path)
        block = MarkdownBlock(
            block_id=block_id,
            type=item["type"],
            source=str(item.get("text") or ""),
            translation=str(target.get("text") or ""),
            heading_level=int(item.get("level") or 0),
            heading_path=[part for part in heading_path if part],
            asset_path=str(item.get("asset_path") or ""),
        )
        if block.type == "media":
            media = media_by_path.get(block.asset_path, {})
            block.media_id = str(media.get("media_id") or media.get("figure_id") or "")
            block.media_type = str(media.get("type") or item.get("alt") or "figure")
            block.page_number = media.get("page_number")
            block.bbox_pdf = list(media.get("bbox_pdf") or [])
            block.caption_source = str(media.get("caption") or "")
        blocks.append(block)

    # A caption is an atomic part of the media immediately before it.
    compact: list[MarkdownBlock] = []
    for block in blocks:
        if block.type == "caption" and compact and compact[-1].type == "media":
            compact[-1].caption_source = block.source or compact[-1].caption_source
            compact[-1].caption_translation = block.translation
            continue
        compact.append(block)

    source_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
    target_digest = hashlib.sha256(target_path.read_bytes()).hexdigest() if target_path.is_file() else ""
    return {
        "version": 1,
        "source_digest": source_digest,
        "target_digest": target_digest,
        "blocks": [asdict(block) for block in compact],
    }
