from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from shared import storage

logger = logging.getLogger("arxiv_retrieval.scheduler")

# 0 = run once (testing / one-shot)
PERIOD_CHOICES = {
    0: "仅一次（测试）",
    1: "每天",
    2: "每两天",
    3: "每三天",
    7: "每一周",
    14: "每两周",
}

IMMEDIATE_PULL_TIME = "now"


def normalize_pull_time(value: str | None) -> str:
    text = (value or "09:00").strip().lower()
    if text in {IMMEDIATE_PULL_TIME, "immediate", "立即"}:
        return IMMEDIATE_PULL_TIME
    _parse_hhmm(text)
    return text


def _parse_hhmm(value: str) -> tuple[int, int]:
    try:
        hour_s, minute_s = value.strip().split(":", 1)
        hour, minute = int(hour_s), int(minute_s)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour, minute
    except Exception as exc:
        raise ValueError("拉取时间应为「立即」或 HH:MM（例如 09:00）") from exc


def _parse_pull_time(value: str) -> tuple[int, int] | None:
    """Backward-compatible helper. Returns None for immediate mode."""
    normalized = normalize_pull_time(value)
    if normalized == IMMEDIATE_PULL_TIME:
        return None
    return _parse_hhmm(normalized)


def is_due(config: dict) -> bool:
    if not config.get("enabled"):
        return False

    tz_name = config.get("timezone") or "Asia/Shanghai"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Asia/Shanghai")

    now = datetime.now(tz)
    pull_time = normalize_pull_time(config.get("pull_time"))
    period_days = int(config.get("period_days") if config.get("period_days") is not None else 1)
    last_run_at = config.get("last_run_at")

    # One-shot: fire at most once while enabled.
    if period_days <= 0:
        if last_run_at:
            return False
        if pull_time == IMMEDIATE_PULL_TIME:
            return True
        hour, minute = _parse_hhmm(pull_time)
        return (now.hour, now.minute) >= (hour, minute)

    # Recurring
    if pull_time != IMMEDIATE_PULL_TIME:
        hour, minute = _parse_hhmm(pull_time)
        if (now.hour, now.minute) < (hour, minute):
            return False

    if not last_run_at:
        return True

    try:
        last = datetime.fromisoformat(last_run_at)
        if last.tzinfo is None:
            last = last.replace(tzinfo=ZoneInfo("UTC"))
        last_local = last.astimezone(tz)
    except Exception:
        return True

    # Immediate + recurring: still respect the period gap.
    return now - last_local >= timedelta(days=max(1, period_days))


class RetrievalScheduler:
    def __init__(
        self,
        category_runner: Callable[[], dict],
        keyword_runner: Callable[[], dict],
    ) -> None:
        self.category_runner = category_runner
        self.keyword_runner = keyword_runner
        self.scheduler = BackgroundScheduler()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self.scheduler.add_job(
            self.tick,
            IntervalTrigger(seconds=15),
            id="arxiv-retrieval-tick",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        self.scheduler.start()
        self._started = True
        logger.info("scheduler started")

    def shutdown(self) -> None:
        if self._started:
            self.scheduler.shutdown(wait=False)
            self._started = False

    def tick(self) -> None:
        self._maybe_run("category", self.category_runner)
        self._maybe_run("keyword", self.keyword_runner)

    def _maybe_run(self, mode: str, runner: Callable[[], dict]) -> None:
        config = storage.load_config(mode)
        if not is_due(config):
            return
        try:
            logger.info("auto-run mode=%s period_days=%s pull_time=%s", mode, config.get("period_days"), config.get("pull_time"))
            runner()
            # One-shot: auto-disable after a successful run.
            latest = storage.load_config(mode)
            if int(latest.get("period_days") or 0) <= 0:
                storage.update_config(
                    mode,
                    {
                        "enabled": False,
                        "last_status": "ok",
                        "last_error": None,
                    },
                )
                logger.info("one-shot complete, disabled mode=%s", mode)
        except Exception as exc:  # noqa: BLE001
            logger.exception("auto-run failed mode=%s", mode)
            patch = {
                "last_run_at": storage.utc_now_iso(),
                "last_status": "error",
                "last_error": str(exc),
            }
            # Keep one-shot enabled on error so user can retry after fixing.
            storage.update_config(mode, patch)
