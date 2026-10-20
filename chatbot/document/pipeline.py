from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .hierarchical_chunker import build_chunks
from .markdown_structure import build_bilingual_structure


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_document_index(task_dir: Path, *, force: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    translation_dir = task_dir / "translation"
    source_path = translation_dir / "input.md"
    target_path = translation_dir / "input.zh.md"
    if not source_path.is_file():
        raise FileNotFoundError("Source Markdown is not ready")
    rag_dir = task_dir / "chatbot"
    rag_dir.mkdir(parents=True, exist_ok=True)
    structure_path = rag_dir / "document_structure.json"
    chunks_path = rag_dir / "chunks.json"
    structure = build_bilingual_structure(
        source_path,
        target_path,
        translation_dir / "media_manifest.json",
    )
    if not force and structure_path.is_file() and chunks_path.is_file():
        try:
            previous = json.loads(structure_path.read_text(encoding="utf-8"))
            if (
                previous.get("source_digest") == structure.get("source_digest")
                and previous.get("target_digest") == structure.get("target_digest")
            ):
                return previous, json.loads(chunks_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    chunks = build_chunks(structure)
    _write_json(structure_path, structure)
    _write_json(chunks_path, chunks)
    return structure, chunks
