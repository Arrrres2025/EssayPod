from __future__ import annotations

import os
from pathlib import Path


ESSAYPOD_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ESSAYPOD_ROOT.parent


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def bootstrap_env() -> None:
    load_dotenv(REPO_ROOT / ".env")
    load_dotenv(ESSAYPOD_ROOT / ".env")


def get_paddle_ocr_base_url() -> str:
    bootstrap_env()
    base_url = os.environ.get("PADDLE_OCR_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise RuntimeError(
            "Missing PADDLE_OCR_BASE_URL in environment (.env). "
            "See REMOTE_LAYOUT_OCR.md for setup."
        )
    return base_url
