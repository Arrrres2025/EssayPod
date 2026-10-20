from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.RLock()

DEFAULT_CATEGORY_CONFIG: dict[str, Any] = {
    "enabled": False,
    "categories": ["cs.CL", "cs.AI"],
    "max_results": 20,
    "period_days": 1,
    "pull_time": "09:00",
    "timezone": "Asia/Shanghai",
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
}

DEFAULT_KEYWORD_CONFIG: dict[str, Any] = {
    "enabled": False,
    "keywords": [
        {"text": "Agent", "enabled": True},
        {"text": "Retrieval", "enabled": True},
        {"text": "RAG", "enabled": True},
    ],
    "match_title": True,
    "match_abstract": True,
    "max_results": 20,
    "period_days": 1,
    "pull_time": "09:00",
    "timezone": "Asia/Shanghai",
    "last_run_at": None,
    "last_status": None,
    "last_error": None,
}


def normalize_keywords(raw: list[Any]) -> list[dict[str, Any]]:
    """Accept legacy string list or [{text, enabled}] objects."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw or []:
        if isinstance(item, str):
            text = item.strip()
            enabled = True
        elif isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            enabled = bool(item.get("enabled", True))
        else:
            continue
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append({"text": text, "enabled": enabled})
    return out


def active_keyword_texts(config: dict[str, Any]) -> list[str]:
    return [k["text"] for k in normalize_keywords(config.get("keywords") or []) if k.get("enabled")]


def _config_path(mode: str) -> Path:
    return DATA_DIR / f"{mode}_config.json"


def _results_path(mode: str) -> Path:
    return DATA_DIR / f"{mode}_latest.json"


def _seen_path(mode: str) -> Path:
    return DATA_DIR / f"{mode}_seen.json"


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_config(mode: str) -> dict[str, Any]:
    default = DEFAULT_CATEGORY_CONFIG if mode == "category" else DEFAULT_KEYWORD_CONFIG
    with _lock:
        data = _read_json(_config_path(mode), None)
        if data is None:
            _write_json(_config_path(mode), default)
            return dict(default)
        merged = dict(default)
        merged.update(data)
        return merged


def save_config(mode: str, config: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        _write_json(_config_path(mode), config)
        return config


def update_config(mode: str, patch: dict[str, Any]) -> dict[str, Any]:
    default = DEFAULT_CATEGORY_CONFIG if mode == "category" else DEFAULT_KEYWORD_CONFIG
    with _lock:
        data = _read_json(_config_path(mode), None)
        current = dict(default)
        if data:
            current.update(data)
        current.update(patch)
        _write_json(_config_path(mode), current)
        return current


def load_latest_results(mode: str) -> dict[str, Any]:
    with _lock:
        return _read_json(
            _results_path(mode),
            {"fetched_at": None, "papers": [], "meta": {}},
        )


def save_latest_results(mode: str, papers: list[dict], meta: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "papers": papers,
        "meta": meta,
    }
    with _lock:
        _write_json(_results_path(mode), payload)
        return payload


def load_seen_ids(mode: str) -> set[str]:
    with _lock:
        data = _read_json(_seen_path(mode), {"ids": []})
        return set(data.get("ids", []))


def mark_seen_ids(mode: str, ids: list[str], keep_last: int = 5000) -> None:
    with _lock:
        existing = _read_json(_seen_path(mode), {"ids": []}).get("ids", [])
        merged = list(dict.fromkeys(list(ids) + existing))[:keep_last]
        _write_json(_seen_path(mode), {"ids": merged})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
