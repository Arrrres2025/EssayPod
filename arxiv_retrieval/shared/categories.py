from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_CATEGORIES_PATH = Path(__file__).with_name("categories.json")


@lru_cache(maxsize=1)
def load_taxonomy() -> dict:
    with _CATEGORIES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def list_groups() -> list[dict]:
    return load_taxonomy()["groups"]


def all_category_ids() -> set[str]:
    ids: set[str] = set()
    for group in list_groups():
        for cat in group["categories"]:
            ids.add(cat["id"])
    return ids


def validate_category_ids(category_ids: list[str]) -> list[str]:
    known = all_category_ids()
    unknown = [c for c in category_ids if c not in known]
    if unknown:
        raise ValueError(f"未知分类: {', '.join(unknown)}")
    return category_ids
