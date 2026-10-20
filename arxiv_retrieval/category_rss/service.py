from __future__ import annotations

from typing import Any

from shared import storage
from shared.categories import validate_category_ids

from .fetcher import fetch_categories


def run_category_fetch(config: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = config or storage.load_config("category")
    categories = validate_category_ids(list(cfg.get("categories") or []))
    if not categories:
        raise ValueError("请至少选择一个 arXiv 分类")

    max_results = int(cfg.get("max_results") or 20)
    papers, meta = fetch_categories(categories, max_results=max_results)

    seen = storage.load_seen_ids("category")
    for paper in papers:
        paper["is_new"] = paper["id"] not in seen

    result = storage.save_latest_results("category", papers, meta)
    storage.mark_seen_ids("category", [p["id"] for p in papers if p.get("id")])
    storage.update_config(
        "category",
        {
            "last_run_at": storage.utc_now_iso(),
            "last_status": "ok",
            "last_error": None,
        },
    )
    return result
