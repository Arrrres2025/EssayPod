from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_LEDGER_PATH = BASE_DIR / "usage" / "token_usage.jsonl"


def ledger_path() -> Path:
    configured = os.environ.get("ESSAYPOD_TOKEN_USAGE_PATH", "").strip()
    return Path(configured).expanduser() if configured else DEFAULT_LEDGER_PATH


def _int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def estimate_tokens(text: str) -> int:
    """Conservative fallback used only when a provider omits usage metadata."""
    if not text:
        return 0
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    remaining = len(re.sub(r"[\s\u3400-\u9fff]", "", text))
    return max(1, round(cjk * 0.9 + remaining / 4))


def usage_from_response(response: dict[str, Any] | None) -> dict[str, int]:
    response = response or {}
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else response
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = next(
        (
            _int(usage.get(key))
            for key in ("prompt_tokens", "input_tokens", "input_text_tokens", "prompt_eval_count")
            if usage.get(key) is not None
        ),
        0,
    )
    output_tokens = next(
        (
            _int(usage.get(key))
            for key in ("completion_tokens", "output_tokens", "output_text_tokens", "output_audio_tokens", "eval_count")
            if usage.get(key) is not None
        ),
        0,
    )
    total_tokens = _int(usage.get("total_tokens")) or input_tokens + output_tokens
    details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    cached_tokens = _int(details.get("cached_tokens")) if isinstance(details, dict) else 0
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
    }


def record_usage(
    *,
    feature: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int | None = None,
    cached_tokens: int = 0,
    estimated: bool = False,
    task_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    input_tokens = _int(input_tokens)
    output_tokens = _int(output_tokens)
    total_tokens = _int(total_tokens) or input_tokens + output_tokens
    cached_tokens = _int(cached_tokens)
    if total_tokens <= 0:
        return None
    now = datetime.now().astimezone()
    entry = {
        "timestamp": now.timestamp(),
        "date": now.date().isoformat(),
        "timezone": str(now.tzinfo),
        "feature": feature.strip() or "unknown",
        "provider": provider.strip() or "unknown",
        "model": model.strip() or "unknown",
        "task_id": task_id.strip(),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": cached_tokens,
        "estimated": bool(estimated),
        "metadata": metadata or {},
    }
    path = ledger_path()
    payload = (json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
    except OSError:
        # Accounting must never make a successful LLM request fail.
        return None
    return entry


def record_response_usage(
    response: dict[str, Any] | None,
    *,
    feature: str,
    provider: str,
    model: str,
    input_text: str = "",
    output_text: str = "",
    task_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    usage = usage_from_response(response)
    estimated = usage["total_tokens"] <= 0
    if estimated:
        usage["input_tokens"] = estimate_tokens(input_text)
        usage["output_tokens"] = estimate_tokens(output_text)
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
    return record_usage(
        feature=feature,
        provider=provider,
        model=model,
        task_id=task_id,
        estimated=estimated,
        metadata=metadata,
        **usage,
    )


def _entries() -> list[dict[str, Any]]:
    path = ledger_path()
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def summarize_day(target: date | None = None) -> dict[str, Any]:
    now = datetime.now().astimezone()
    target = target or now.date()
    all_entries = _entries()
    selected = [entry for entry in all_entries if entry.get("date") == target.isoformat()]
    by_feature: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
    )
    by_provider: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
    )
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_tokens": 0,
        "actual_tokens": 0,
        "estimated_tokens": 0,
    }
    for entry in selected:
        values = {
            "input_tokens": _int(entry.get("input_tokens")),
            "output_tokens": _int(entry.get("output_tokens")),
            "total_tokens": _int(entry.get("total_tokens")),
        }
        for key, value in values.items():
            totals[key] += value
        totals["cached_tokens"] += _int(entry.get("cached_tokens"))
        confidence_key = "estimated_tokens" if entry.get("estimated") else "actual_tokens"
        totals[confidence_key] += values["total_tokens"]
        for group, name in ((by_feature, str(entry.get("feature") or "unknown")), (by_provider, str(entry.get("provider") or "unknown"))):
            group[name]["calls"] += 1
            for key, value in values.items():
                group[name][key] += value

    tracking_since = min((_int(entry.get("timestamp")) for entry in all_entries), default=0)
    return {
        "date": target.isoformat(),
        "timezone": str(now.tzinfo),
        **totals,
        "calls": len(selected),
        "all_actual": totals["estimated_tokens"] == 0,
        "tracking_since": tracking_since,
        "by_feature": [
            {"name": name, **values}
            for name, values in sorted(by_feature.items(), key=lambda item: item[1]["total_tokens"], reverse=True)
        ],
        "by_provider": [
            {"name": name, **values}
            for name, values in sorted(by_provider.items(), key=lambda item: item[1]["total_tokens"], reverse=True)
        ],
    }
