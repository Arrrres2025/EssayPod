from __future__ import annotations

import time
import urllib.parse
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from shared.papers import dedupe_papers, parse_feed, sort_papers_by_date

USER_AGENT = "EssayPod-ArxivRetrieval/0.1 (category-rss; local research tool)"
RSS_URL = "https://rss.arxiv.org/rss/{category}"
API_URL = "https://export.arxiv.org/api/query"


def _get(url: str, timeout: int = 25, retries: int = 2) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if resp.status_code == 429 or resp.text.strip() == "Rate exceeded.":
                last = RuntimeError("arXiv 限流 (429)，请稍后再试")
                time.sleep(3 + attempt * 3)
                continue
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1 + attempt)
    raise RuntimeError(str(last) if last else f"请求失败: {url}") from last


def _rss_likely_empty() -> bool:
    """arXiv RSS skips Saturday/Sunday (US Eastern)."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    return now_et.weekday() >= 5  # 5=Sat, 6=Sun


def fetch_category_rss(category: str, timeout: int = 20) -> list[dict[str, Any]]:
    url = RSS_URL.format(category=category)
    resp = _get(url, timeout=timeout)
    return parse_feed(resp.content, source=f"rss:{category}")


def fetch_categories_api(
    categories: list[str],
    max_results: int = 20,
    timeout: int = 45,
) -> list[dict[str, Any]]:
    """One Query API call for multiple categories."""
    query = " OR ".join(f"cat:{c}" for c in categories)
    params = {
        "search_query": query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max(1, min(int(max_results), 100)),
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    resp = _get(url, timeout=timeout)
    return parse_feed(resp.content, source="api:multi-cat")


def fetch_categories(
    categories: list[str],
    max_results: int = 20,
    pause_seconds: float = 3.0,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    errors: dict[str, str] = {}
    per_category_counts: dict[str, int] = {}
    sources_used: dict[str, str] = {}
    all_papers: list[dict[str, Any]] = []

    # Weekend: skip empty RSS and do a single API query (much faster / less rate-limit).
    if _rss_likely_empty():
        try:
            papers = fetch_categories_api(categories, max_results=max_results)
            all_papers = papers
            for cat in categories:
                per_category_counts[cat] = sum(
                    1 for p in papers if cat in (p.get("categories") or [])
                )
                sources_used[cat] = "api-weekend"
        except Exception as exc:  # noqa: BLE001
            for cat in categories:
                errors[cat] = str(exc)
                per_category_counts[cat] = 0
                sources_used[cat] = "error"
    else:
        for i, cat in enumerate(categories):
            try:
                papers = fetch_category_rss(cat)
                source = "rss"
                if not papers:
                    time.sleep(pause_seconds)
                    papers = fetch_categories_api([cat], max_results=max_results)
                    source = "api-fallback"
                per_category_counts[cat] = len(papers)
                sources_used[cat] = source
                all_papers.extend(papers)
            except Exception as exc:  # noqa: BLE001
                errors[cat] = str(exc)
                per_category_counts[cat] = 0
                sources_used[cat] = "error"
            if i < len(categories) - 1:
                time.sleep(pause_seconds)

    merged = sort_papers_by_date(dedupe_papers(all_papers))
    limited = merged[: max(1, int(max_results))]
    meta = {
        "mode": "category",
        "categories": categories,
        "requested_max_results": max_results,
        "raw_total": len(merged),
        "returned": len(limited),
        "per_category_counts": per_category_counts,
        "sources_used": sources_used,
        "errors": errors,
        "note": "周末 RSS 为空时自动走 Query API；多分类合并为一次请求。",
    }
    return limited, meta
