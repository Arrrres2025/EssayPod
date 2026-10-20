from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from token_usage import record_response_usage

try:
    from condense_agent import load_dotenv, open_with_proxy_fallback
except ImportError:
    load_dotenv = None  # type: ignore[assignment,misc]
    open_with_proxy_fallback = None  # type: ignore[assignment,misc]

DEFAULT_ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
DEFAULT_ARK_TEXT_MODEL = "doubao-seed-1-6-250615"


def load_essaypod_env() -> None:
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parents[2]
    essaypod_root = Path(__file__).resolve().parents[1]
    load_dotenv(repo_root / ".env")
    load_dotenv(essaypod_root / ".env")


def get_ark_config() -> dict[str, str | float | int]:
    load_essaypod_env()
    return {
        "ark_api_key": os.environ.get("ARK_API_KEY", ""),
        "ark_model": os.environ.get("ARK_TEXT_MODEL", DEFAULT_ARK_TEXT_MODEL),
        "ark_base_url": os.environ.get("ARK_BASE_URL", DEFAULT_ARK_BASE_URL),
        "temperature": float(os.environ.get("PODCAST_AGENT_TEMPERATURE", "0.2")),
        "max_tokens": int(os.environ.get("PODCAST_AGENT_MAX_TOKENS", "2000")),
        "timeout": int(os.environ.get("PODCAST_AGENT_TIMEOUT", "180")),
    }


def call_ark_chat(
    prompt: str,
    *,
    ark_api_key: str,
    ark_model: str,
    ark_base_url: str,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    timeout: int = 180,
    feature: str = "video_key_points",
    task_id: str = "",
) -> str:
    if open_with_proxy_fallback is None:
        raise RuntimeError("condense_agent module not available for Ark HTTP client")
    if not ark_api_key:
        raise RuntimeError("Missing ARK_API_KEY")

    payload = {
        "model": ark_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    url = ark_base_url.rstrip("/") + "/chat/completions"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ark_api_key}",
        },
        method="POST",
    )
    try:
        body = json.loads(open_with_proxy_fallback(request, timeout).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ark HTTP {exc.code}: {detail}") from exc

    try:
        content = body["choices"][0]["message"]["content"]
    except Exception as exc:
        raise RuntimeError(f"Unexpected Ark response: {body}") from exc
    content = str(content).strip()
    if not content:
        raise RuntimeError("Ark returned empty content")
    record_response_usage(
        body,
        feature=feature,
        provider="ark",
        model=ark_model,
        input_text=prompt,
        output_text=content,
        task_id=task_id,
    )
    return content
