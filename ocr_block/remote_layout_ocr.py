"""Remote Paddle Layout OCR client — see REMOTE_LAYOUT_OCR.md."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests

HEADERS = {"ngrok-skip-browser-warning": "true"}


def normalize_base_url(url: str) -> str:
    return url.strip().rstrip("/")


def submit_pdf_job(
    base_url: str,
    pdf_path: Path,
    *,
    mode: str = "accurate",
    device: str = "gpu",
    det_limit_side_len: int | None = None,
    det_limit_type: str | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    data: dict[str, str] = {
        "mode": mode,
        "device": device,
        "use_layout": "true",
    }
    if det_limit_side_len is not None:
        data["det_limit_side_len"] = str(det_limit_side_len)
    if det_limit_type:
        data["det_limit_type"] = det_limit_type

    with pdf_path.open("rb") as handle:
        response = requests.post(
            f"{normalize_base_url(base_url)}/ocr/pdf/jobs",
            data=data,
            files={"file": (pdf_path.name, handle, "application/pdf")},
            headers=HEADERS,
            timeout=timeout,
        )
    response.raise_for_status()
    return response.json()


def poll_job(
    base_url: str,
    job_id: str,
    *,
    poll_interval: float = 3.0,
    timeout_seconds: float = 1800,
    request_timeout: int = 120,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = requests.get(
            f"{normalize_base_url(base_url)}/ocr/jobs/{job_id}",
            headers=HEADERS,
            timeout=request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        status = str(payload.get("status") or "").lower()
        if status in {"completed", "failed"}:
            return payload
        time.sleep(poll_interval)
    raise TimeoutError(f"OCR job {job_id} exceeded {timeout_seconds} seconds.")


def has_layout_result(result: dict[str, Any]) -> bool:
    return any(page.get("layout_blocks") for page in result.get("pages") or [])


def ocr_pdf(
    base_url: str,
    pdf_path: Path,
    *,
    mode: str = "accurate",
    device: str = "gpu",
    poll_interval: float = 3.0,
    timeout_seconds: float = 1800,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Submit PDF, poll until done. Returns (submit_response, completed_result)."""
    submit = submit_pdf_job(base_url, pdf_path, mode=mode, device=device)
    job_id = str(submit["job_id"])
    result = poll_job(
        base_url,
        job_id,
        poll_interval=poll_interval,
        timeout_seconds=timeout_seconds,
    )
    if result.get("status") != "completed":
        raise RuntimeError(f"OCR failed: {result.get('error')}")
    if not has_layout_result(result):
        raise RuntimeError("OCR completed without layout_blocks; check use_layout=true")
    return submit, result
