from __future__ import annotations

import time
import urllib.parse
from typing import Any

import requests

from shared.papers import dedupe_papers, parse_feed, sort_papers_by_date

USER_AGENT = "EssayPod-ArxivRetrieval/0.1 (keyword-query; local research tool)"
API_URL = "https://export.arxiv.org/api/query"


def _get(url: str, timeout: int = 45, retries: int = 2) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            if resp.status_code == 429 or resp.text.strip() == "Rate exceeded.":
                last = RuntimeError("arXiv 限流 (429)，请稍后再试")
                time.sleep(5 + attempt * 5)
                continue
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 + attempt)
    raise RuntimeError("Query API 请求失败（可能触发限流，请稍后再试）") from last


def _quote_term(term: str) -> str:
    term = term.strip()
    if not term:
        return ""
    if " " in term or "-" in term:
        return f'"{term}"'
    return term


def build_search_query(
    keywords: list[str],
    *,
    match_title: bool = True,
    match_abstract: bool = True,
) -> str:
    fields: list[str] = []
    if match_title:
        fields.append("ti")
    if match_abstract:
        fields.append("abs")
    if not fields:
        fields = ["all"]

    clauses: list[str] = []
    for raw in keywords:
        term = _quote_term(raw)
        if not term:
            continue
        field_parts = [f"{field}:{term}" for field in fields]
        if len(field_parts) == 1:
            clauses.append(field_parts[0])
        else:
            clauses.append("(" + " OR ".join(field_parts) + ")")

    if not clauses:
        raise ValueError("请至少输入一个关键词")
    return " OR ".join(clauses)


def fetch_by_keywords(
    keywords: list[str],
    *,
    max_results: int = 20,
    match_title: bool = True,
    match_abstract: bool = True,
    timeout: int = 45,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    search_query = build_search_query(
        keywords,
        match_title=match_title,
        match_abstract=match_abstract,
    )
    params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "start": 0,
        "max_results": max(1, min(int(max_results), 100)),
    }
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    resp = _get(url, timeout=timeout)
    papers = sort_papers_by_date(dedupe_papers(parse_feed(resp.content, source="query-api")))
    limited = papers[: max(1, int(max_results))]
    meta = {
        "mode": "keyword",
        "keywords": keywords,
        "search_query": search_query,
        "request_url": url,
        "requested_max_results": max_results,
        "returned": len(limited),
    }
    return limited, meta
