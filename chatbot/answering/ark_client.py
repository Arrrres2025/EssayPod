from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from token_usage import record_response_usage


def config() -> tuple[str, str, str]:
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        raise RuntimeError("Missing ARK_API_KEY")
    base_url = os.environ.get(
        "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
    ).rstrip("/")
    model = os.environ.get("ARK_TEXT_MODEL") or os.environ.get(
        "ARK_MODEL", "doubao-seed-1-6-250615"
    )
    return api_key, base_url, model


def complete(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.1,
    max_tokens: int = 1200,
    feature: str = "paper_chat_planning",
    task_id: str = "",
) -> str:
    api_key, base_url, model = config()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        # Reranking/query rewriting are optional quality passes. Keep them
        # bounded so a slow auxiliary request never blocks the first answer.
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=25) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = str(body["choices"][0]["message"]["content"])
        record_response_usage(
            body,
            feature=feature,
            provider="ark",
            model=model,
            input_text=json.dumps(messages, ensure_ascii=False),
            output_text=content,
            task_id=task_id,
        )
        return content
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ark HTTP {exc.code}: {detail}") from exc


def stream(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.15,
    max_tokens: int = 2400,
    feature: str = "paper_chat_answer",
    task_id: str = "",
) -> Iterator[str]:
    api_key, base_url, model = config()
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
        },
        method="POST",
    )
    output_parts: list[str] = []
    usage_payload: dict[str, Any] = {}
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=180) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if isinstance(event.get("usage"), dict):
                    usage_payload = {"usage": event["usage"]}
                choices = event.get("choices") or [{}]
                delta = choices[0].get("delta", {}).get("content", "")
                if isinstance(delta, str) and delta:
                    output_parts.append(delta)
                    yield delta
        record_response_usage(
            usage_payload,
            feature=feature,
            provider="ark",
            model=model,
            input_text=json.dumps(messages, ensure_ascii=False),
            output_text="".join(output_parts),
            task_id=task_id,
        )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ark stream HTTP {exc.code}: {detail}") from exc
