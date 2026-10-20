from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any


class ConversationStore:
    def __init__(self, task_dir: Path):
        self.directory = task_dir / "chatbot" / "conversations"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def _path(self, conversation_id: str) -> Path:
        safe = "".join(character for character in conversation_id if character.isalnum() or character in "-_")
        if not safe:
            raise ValueError("Invalid conversation id")
        return self.directory / f"{safe}.json"

    def create(self) -> dict[str, Any]:
        conversation = {
            "id": uuid.uuid4().hex,
            "title": "论文问答",
            "messages": [],
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self.save(conversation)
        return conversation

    def load(self, conversation_id: str) -> dict[str, Any]:
        path = self._path(conversation_id)
        if not path.is_file():
            return self.create()
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, conversation: dict[str, Any]) -> None:
        conversation["updated_at"] = time.time()
        with self.lock:
            self._path(str(conversation["id"])).write_text(
                json.dumps(conversation, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def list(self) -> list[dict[str, Any]]:
        conversations: list[dict[str, Any]] = []
        for path in self.directory.glob("*.json"):
            try:
                conversations.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return sorted(conversations, key=lambda item: item.get("updated_at", 0), reverse=True)
