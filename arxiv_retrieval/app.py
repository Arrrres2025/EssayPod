from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from category_rss.service import run_category_fetch
from keyword_query.service import run_keyword_fetch
from shared import storage
from shared.categories import list_groups, validate_category_ids
from shared.scheduler import (
    IMMEDIATE_PULL_TIME,
    PERIOD_CHOICES,
    RetrievalScheduler,
    normalize_pull_time,
)
from shared.storage import normalize_keywords

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("arxiv_retrieval")

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"

app = FastAPI(title="EssayPod arXiv Retrieval", version="0.1.0")
scheduler = RetrievalScheduler(
    category_runner=lambda: run_category_fetch(),
    keyword_runner=lambda: run_keyword_fetch(),
)


class CategoryConfigIn(BaseModel):
    enabled: bool = False
    categories: list[str] = Field(default_factory=list)
    max_results: int = Field(default=20, ge=1, le=100)
    period_days: int = Field(default=1, ge=0, le=30)
    pull_time: str = "09:00"
    timezone: str = "Asia/Shanghai"


class KeywordItem(BaseModel):
    text: str
    enabled: bool = True


class KeywordConfigIn(BaseModel):
    enabled: bool = False
    keywords: list[KeywordItem] = Field(default_factory=list)
    match_title: bool = True
    match_abstract: bool = True
    max_results: int = Field(default=20, ge=1, le=100)
    period_days: int = Field(default=1, ge=0, le=30)
    pull_time: str = "09:00"
    timezone: str = "Asia/Shanghai"


def _prepare_schedule_fields(
    body_enabled: bool,
    period_days: int,
    pull_time: str,
    *,
    rearm: bool = True,
) -> dict[str, Any]:
    """Normalize schedule fields; arm one-shot / immediate runs for testing."""
    normalized_time = normalize_pull_time(pull_time)
    patch: dict[str, Any] = {
        "pull_time": normalized_time,
        "period_days": int(period_days),
    }
    # Re-arm one-shot when user enables it again (useful for repeated tests).
    if rearm and body_enabled and int(period_days) <= 0:
        patch["last_run_at"] = None
        patch["last_status"] = None
        patch["last_error"] = None
    return patch


@app.on_event("startup")
def _startup() -> None:
    storage.load_config("category")
    storage.load_config("keyword")
    scheduler.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    scheduler.shutdown()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {
        "period_choices": [
            {"days": days, "label": label} for days, label in PERIOD_CHOICES.items()
        ],
        "taxonomy": list_groups(),
    }


@app.get("/api/category/config")
def get_category_config() -> dict[str, Any]:
    return storage.load_config("category")


@app.put("/api/category/config")
def put_category_config(body: CategoryConfigIn, rearm: bool = True) -> dict[str, Any]:
    try:
        categories = validate_category_ids(body.categories)
        schedule = _prepare_schedule_fields(
            body.enabled, body.period_days, body.pull_time, rearm=rearm
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    current = storage.load_config("category")
    current.update(body.model_dump())
    current.update(schedule)
    current["categories"] = categories
    return storage.save_config("category", current)


@app.post("/api/category/run")
def run_category_now() -> dict[str, Any]:
    try:
        return run_category_fetch()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        storage.update_config(
            "category",
            {
                "last_run_at": storage.utc_now_iso(),
                "last_status": "error",
                "last_error": str(exc),
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/category/results")
def get_category_results() -> dict[str, Any]:
    return storage.load_latest_results("category")


@app.get("/api/keyword/config")
def get_keyword_config() -> dict[str, Any]:
    cfg = storage.load_config("keyword")
    cfg["keywords"] = normalize_keywords(cfg.get("keywords") or [])
    return cfg


@app.put("/api/keyword/config")
def put_keyword_config(body: KeywordConfigIn, rearm: bool = True) -> dict[str, Any]:
    keywords = normalize_keywords([k.model_dump() for k in body.keywords])
    active = [k for k in keywords if k.get("enabled")]
    if body.enabled and not active:
        raise HTTPException(status_code=400, detail="启用前请至少勾选一个关键词")
    try:
        schedule = _prepare_schedule_fields(
            body.enabled, body.period_days, body.pull_time, rearm=rearm
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not body.match_title and not body.match_abstract:
        raise HTTPException(status_code=400, detail="请至少选择标题或摘要作为匹配字段")

    current = storage.load_config("keyword")
    current.update(body.model_dump())
    current.update(schedule)
    current["keywords"] = keywords
    return storage.save_config("keyword", current)


@app.post("/api/keyword/run")
def run_keyword_now() -> dict[str, Any]:
    try:
        return run_keyword_fetch()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        storage.update_config(
            "keyword",
            {
                "last_run_at": storage.utc_now_iso(),
                "last_status": "error",
                "last_error": str(exc),
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/keyword/results")
def get_keyword_results() -> dict[str, Any]:
    return storage.load_latest_results("keyword")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def main() -> None:
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8787, reload=False)


if __name__ == "__main__":
    main()
