from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from chatbot.document import build_document_index
from chatbot.retrieval import HybridPaperIndex


def state_path(task_dir: Path) -> Path:
    return task_dir / "chatbot" / "state.json"


def write_state(task_dir: Path, status: str, **fields: Any) -> dict[str, Any]:
    path = state_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"status": status, "updated_at": time.time(), **fields}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def read_state(task_dir: Path) -> dict[str, Any]:
    path = state_path(task_dir)
    if not path.is_file():
        return {"status": "idle", "ready": False, "error": "", "message": "等待论文结构"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        payload = {"status": "failed", "error": "Invalid chatbot state"}
    payload["ready"] = (
        payload.get("status") == "ready"
        and (task_dir / "chatbot" / "document_structure.json").is_file()
        and (task_dir / "chatbot" / "embeddings.npy").is_file()
    )
    return payload


def build_task_index(task_dir: Path, *, force: bool = False) -> dict[str, Any]:
    write_state(task_dir, "building", message="正在按论文标题层级建立索引", error="")
    try:
        structure, chunks = build_document_index(task_dir, force=force)
        index_state = HybridPaperIndex.build(task_dir / "chatbot", force=force)
        return write_state(
            task_dir,
            "ready",
            ready=True,
            message="论文问答已就绪",
            error="",
            block_count=len(structure.get("blocks", [])),
            chunk_count=len(chunks),
            embedding_model=index_state.get("embedding_model"),
        )
    except Exception as exc:
        write_state(task_dir, "failed", ready=False, message="索引建立失败", error=str(exc))
        raise
