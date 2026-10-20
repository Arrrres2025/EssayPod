from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Config:
    root: Path
    provider: str
    model: str
    base_url: str
    api_key: str
    host: str
    port: int
    max_upload_bytes: int
    max_source_chars: int
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    ark_max_attempts: int = 2
    request_timeout_seconds: float = 90.0
    deepseek_request_timeout_seconds: float = 180.0

    @classmethod
    def load(cls, root: Path | None = None) -> "Config":
        project_root = (root or Path.cwd()).resolve()
        load_dotenv(project_root / ".env")
        # EssayPod keeps provider credentials in text2vadio/.env. A module-local
        # .env still takes precedence because load_dotenv uses setdefault.
        if project_root.parent.name == "EssayPod":
            load_dotenv(project_root.parent.parent / ".env")
        explicit_provider = os.getenv("LLM_WIKI_PROVIDER", "").strip().lower()
        provider = explicit_provider or (
            "ark"
            if os.getenv("ARK_API_KEY", "").strip()
            else (
                "deepseek"
                if os.getenv("DEEPSEEK_API_KEY", "").strip()
                else "mock"
            )
        )
        if provider == "ollama":
            default_base = "http://127.0.0.1:11434"
            default_model = "llama3.1:8b"
        elif provider == "ark":
            default_base = os.getenv(
                "ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
            )
            # Online-inference ID for the flagship Doubao Seed 2.0 Pro.
            default_model = "doubao-seed-2-0-pro-260215"
        elif provider == "deepseek":
            default_base = os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            )
            default_model = (
                os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-v4-pro"
            )
        else:
            default_base = "https://api.openai.com"
            default_model = "llama3.1:8b"
        return cls(
            root=project_root,
            provider=provider,
            model=(
                os.getenv("LLM_WIKI_MODEL", "").strip()
                or (
                    os.getenv("ARK_TEXT_MODEL", "").strip()
                    if provider == "ark"
                    else ""
                )
                or default_model
            ),
            base_url=(
                os.getenv("LLM_WIKI_BASE_URL", "").strip() or default_base
            ).rstrip("/"),
            api_key=os.getenv("LLM_WIKI_API_KEY", "").strip()
            or (
                os.getenv("ARK_API_KEY", "").strip()
                if provider == "ark"
                else (
                    os.getenv("DEEPSEEK_API_KEY", "").strip()
                    if provider == "deepseek"
                    else ""
                )
            ),
            host=os.getenv("LLM_WIKI_HOST", "127.0.0.1"),
            port=int(os.getenv("LLM_WIKI_PORT", "8765")),
            # EssayPod may already have accepted and stored image-heavy papers
            # whose PDF container is much larger than the extracted text sent to
            # the model. Keep a finite safety limit, but do not reject ordinary
            # local papers at the old 25 MiB bridge boundary.
            max_upload_bytes=int(os.getenv("LLM_WIKI_MAX_UPLOAD_MB", "100"))
            * 1024
            * 1024,
            max_source_chars=int(
                os.getenv("LLM_WIKI_MAX_SOURCE_CHARS", "120000")
            ),
            tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
            tavily_base_url=os.getenv(
                "TAVILY_BASE_URL", "https://api.tavily.com"
            ).strip().rstrip("/"),
            deepseek_api_key=os.getenv("DEEPSEEK_API_KEY", "").strip(),
            deepseek_base_url=os.getenv(
                "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
            ).strip().rstrip("/"),
            deepseek_model=(
                os.getenv("DEEPSEEK_MODEL", "").strip() or "deepseek-v4-pro"
            ),
            ark_max_attempts=max(
                1, int(os.getenv("LLM_WIKI_ARK_MAX_ATTEMPTS", "2"))
            ),
            request_timeout_seconds=min(
                180.0,
                max(
                    20.0,
                    float(os.getenv("LLM_WIKI_REQUEST_TIMEOUT_SECONDS", "90")),
                ),
            ),
            deepseek_request_timeout_seconds=min(
                300.0,
                max(
                    30.0,
                    float(
                        os.getenv(
                            "LLM_WIKI_DEEPSEEK_TIMEOUT_SECONDS", "180"
                        )
                    ),
                ),
            ),
        )
