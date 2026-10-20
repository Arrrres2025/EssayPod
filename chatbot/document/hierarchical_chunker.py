from __future__ import annotations

import re
from typing import Any

try:
    import tiktoken
except ImportError:  # pragma: no cover
    tiktoken = None


TARGET_TOKENS = 820
MAX_TOKENS = 1024
MIN_TOKENS = 220
OVERLAP_TOKENS = 128


def token_count(text: str) -> int:
    if tiktoken is not None:
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return cjk + max(1, len(text) - cjk) // 4


def _trim_to_tokens(text: str, limit: int, *, from_end: bool = False) -> str:
    if not text or limit <= 0:
        return ""
    if tiktoken is not None:
        encoding = tiktoken.get_encoding("cl100k_base")
        ids = encoding.encode(text)
        ids = ids[-limit:] if from_end else ids[:limit]
        return encoding.decode(ids)
    chars = max(1, limit * 3)
    return text[-chars:] if from_end else text[:chars]


def _block_text(block: dict[str, Any], language: str) -> str:
    text = str(block.get("translation" if language == "zh" else "source") or "")
    if block.get("type") == "media":
        caption = str(
            block.get("caption_translation" if language == "zh" else "caption_source")
            or block.get("caption_source")
            or ""
        )
        media_type = str(block.get("media_type") or "media")
        media_id = str(block.get("media_id") or "")
        return f"[{media_type} {media_id}] {caption}".strip()
    return text


def _split_long_block(block: dict[str, Any]) -> list[dict[str, Any]]:
    source = str(block.get("source") or "")
    target = str(block.get("translation") or "")
    if token_count(source) <= TARGET_TOKENS:
        return [block]
    source_sentences = re.split(r"(?<=[.!?。！？])\s+", source)
    target_sentences = re.split(r"(?<=[.!?。！？])\s*", target) if target else []
    pieces: list[dict[str, Any]] = []
    current: list[str] = []
    for sentence in source_sentences:
        if current and token_count(" ".join(current + [sentence])) > TARGET_TOKENS:
            item = dict(block)
            item["source"] = " ".join(current)
            ratio_start = len(pieces) / max(1, len(source_sentences))
            ratio_end = (len(pieces) + 1) / max(1, len(source_sentences))
            item["translation"] = "".join(
                target_sentences[
                    int(ratio_start * len(target_sentences)) : int(ratio_end * len(target_sentences))
                ]
            )
            pieces.append(item)
            current = []
        current.append(sentence)
    if current:
        item = dict(block)
        item["source"] = " ".join(current)
        item["translation"] = "".join(target_sentences[len(pieces) :]) if target_sentences else target
        pieces.append(item)
    return pieces


def build_chunks(structure: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for block in structure.get("blocks", []):
        blocks.extend(_split_long_block(block))

    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_h2 = ""
    previous_tail_source = ""
    previous_tail_zh = ""

    def flush() -> None:
        nonlocal current, previous_tail_source, previous_tail_zh
        content = [block for block in current if block.get("type") != "heading"]
        if not content:
            current = []
            return
        heading_path = list(content[0].get("heading_path") or [])
        source_body = "\n\n".join(filter(None, (_block_text(block, "source") for block in content)))
        zh_body = "\n\n".join(filter(None, (_block_text(block, "zh") for block in content)))
        breadcrumb = " > ".join(heading_path)
        source_text = f"Section: {breadcrumb}\n\n{source_body}" if breadcrumb else source_body
        zh_text = f"章节：{breadcrumb}\n\n{zh_body}" if breadcrumb else zh_body
        if chunks and previous_tail_source:
            source_text = f"Previous context:\n{previous_tail_source}\n\n{source_text}"
            if previous_tail_zh:
                zh_text = f"上文：\n{previous_tail_zh}\n\n{zh_text}"
        if token_count(source_text) > MAX_TOKENS:
            source_text = _trim_to_tokens(source_text, MAX_TOKENS)
        media = [
            {
                "media_id": block.get("media_id"),
                "media_type": block.get("media_type"),
                "asset_path": block.get("asset_path"),
                "caption_source": block.get("caption_source"),
                "caption_translation": block.get("caption_translation"),
                "page_number": block.get("page_number"),
            }
            for block in content
            if block.get("type") == "media"
        ]
        chunks.append(
            {
                "chunk_id": f"chunk_{len(chunks) + 1:04d}",
                "heading_path": heading_path,
                "block_ids": list(dict.fromkeys(str(block["block_id"]) for block in content)),
                "source_text": source_text,
                "zh_text": zh_text,
                "token_count": token_count(source_text),
                "media": media,
            }
        )
        previous_tail_source = _trim_to_tokens(source_body, OVERLAP_TOKENS, from_end=True)
        previous_tail_zh = _trim_to_tokens(zh_body, OVERLAP_TOKENS, from_end=True)
        current = []

    for block in blocks:
        if block.get("type") == "heading":
            level = int(block.get("heading_level") or 0)
            if level == 2:
                if current:
                    flush()
                current_h2 = str(block.get("source") or "")
            elif level == 3 and current:
                size = token_count(
                    "\n\n".join(_block_text(item, "source") for item in current)
                )
                if size >= MIN_TOKENS:
                    flush()
            continue
        proposed = current + [block]
        proposed_size = token_count(
            "\n\n".join(_block_text(item, "source") for item in proposed)
        )
        if current and proposed_size > MAX_TOKENS:
            flush()
        current.append(block)
        if token_count("\n\n".join(_block_text(item, "source") for item in current)) >= TARGET_TOKENS:
            flush()
    flush()
    return chunks
