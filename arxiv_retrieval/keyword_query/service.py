from __future__ import annotations

from typing import Any

from shared import storage
from shared.storage import active_keyword_texts

from .fetcher import fetch_by_keywords


def run_keyword_fetch(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or storage.load_config("keyword")
    keywords = active_keyword_texts(cfg)
    if not keywords:
        raise ValueError("请至少勾选一个启用的关键词")

    papers, meta = fetch_by_keywords(
        keywords,
        max_results=int(cfg.get("max_results") or 20),
        match_title=bool(cfg.get("match_title", True)),
        match_abstract=bool(cfg.get("match_abstract", True)),
    )

    seen = storage.load_seen_ids("keyword")
    for paper in papers:
        paper["is_new"] = paper["id"] not in seen

    result = storage.save_latest_results("keyword", papers, meta)
    storage.mark_seen_ids("keyword", [p["id"] for p in papers if p.get("id")])
    storage.update_config(
        "keyword",
        {
            "last_run_at": storage.utc_now_iso(),
            "last_status": "ok",
            "last_error": None,
        },
    )
    return result
