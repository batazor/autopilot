"""Scenario load-failure state (Redis-backed, scheduler-write / API-read).

A malformed scenario YAML or an unresolvable task factory used to leave only a
log line behind — the scenario just vanished from the schedule. For an
unattended bot that is worse than a crash, so the scheduler now publishes every
load/expand failure here and the dashboard renders a red banner until the file
is fixed.

Shape: one Redis hash, ``wos:system:load_failures``, field = producer source
(``scenario_loader``, ``evaluator``), value = JSON list of entries::

    {"file": "...", "error": "...", "ts": 1731030302.1}          # loader
    {"scenario": "...", "task": "...", "error": "...", "ts": …}  # evaluator

Producers overwrite their own field on every reload/tick and delete it when
they have nothing to report, so the banner self-heals once the YAML is fixed.
Failures are **state**, not events — unlike ``dashboard.notifications`` they
must survive until resolved, hence a hash instead of a capped list.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

LOAD_FAILURES_KEY = "wos:system:load_failures"


def record_load_failures(
    sync_redis_client: Any | None,
    source: str,
    failures: list[dict[str, Any]],
) -> None:
    """Replace ``source``'s failure list (sync producer, e.g. loader reload).

    Redis errors are swallowed — reporting must never take down the scheduler
    that is doing the reporting.
    """
    if sync_redis_client is None:
        return
    try:
        if failures:
            sync_redis_client.hset(
                LOAD_FAILURES_KEY, source, json.dumps(failures, ensure_ascii=False)
            )
        else:
            sync_redis_client.hdel(LOAD_FAILURES_KEY, source)
    except Exception:
        logger.warning("record_load_failures(%s): redis write failed", source, exc_info=True)


async def record_load_failures_async(
    redis_client: Any | None,
    source: str,
    failures: list[dict[str, Any]],
) -> None:
    """Async sibling of :func:`record_load_failures` (scheduler tick loop)."""
    if redis_client is None:
        return
    try:
        if failures:
            await redis_client.hset(
                LOAD_FAILURES_KEY, source, json.dumps(failures, ensure_ascii=False)
            )
        else:
            await redis_client.hdel(LOAD_FAILURES_KEY, source)
    except Exception:
        logger.warning(
            "record_load_failures_async(%s): redis write failed", source, exc_info=True
        )


def read_load_failures(sync_redis_client: Any) -> list[dict[str, Any]]:
    """Flatten all sources into one list (newest first), tagging each entry
    with its ``source``. Used by ``GET /api/load-failures``."""
    try:
        raw = sync_redis_client.hgetall(LOAD_FAILURES_KEY) or {}
    except Exception:
        logger.debug("read_load_failures: redis read failed", exc_info=True)
        return []

    out: list[dict[str, Any]] = []
    for field, value in raw.items():
        source = field.decode() if isinstance(field, bytes) else str(field)
        text = value.decode() if isinstance(value, bytes) else str(value)
        try:
            entries = json.loads(text)
        except Exception:
            continue
        if not isinstance(entries, list):
            continue
        out.extend(
            {"source": source, **entry} for entry in entries if isinstance(entry, dict)
        )

    def _ts(entry: dict[str, Any]) -> float:
        try:
            return float(entry.get("ts") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    out.sort(key=_ts, reverse=True)
    return out
