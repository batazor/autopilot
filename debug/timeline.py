"""Bounded per-instance debug timeline.

Single Redis LIST per instance at ``wos:debug:timeline:<instance_id>`` —
mirrors the house style used by ``ui/notifications.py`` and the queue-history
key in ``worker/instance_worker_tasks.py``: ``LPUSH`` + ``LTRIM`` + ``EXPIRE``.

Why
---
The same task previously left tracks in 4 places — queue history, UI
notifications, ``wos:instance:*:state``, and stdout logs — and operators
had to manually correlate them. The timeline collapses that into one
chronological stream keyed by ``task_id``, so opening a task in the UI
yields the full chain: overlay match → enqueue → pop → dsl steps →
approval gates → finish/fail/preempt.

Correlation
-----------
Every event carries ``task_id`` when one exists. Two event families fire
*before* a task is born and therefore have an empty ``task_id``:

- ``overlay.matched`` / ``overlay.throttled`` — the operator pushed (or
  suppressed) a scenario; the task_id is only minted at queue time.
- ``queue.duplicate_skipped`` — the dedup gate refused to enqueue.

The UI joins those to their downstream task by
``(task_type, region, instance_id, ts ± 2s)``.

Volume
------
Cap is 5_000 entries with a 1h idle TTL. Active play emits ~50-200
events/min; the cap gives operators ~30-90 minutes of history before the
ring buffer rotates — plenty for "what just happened" debugging without
unbounded RAM.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Iterable

logger = logging.getLogger(__name__)

MAX_TIMELINE_EVENTS = 5_000
RETENTION_SECONDS = 60 * 60

# Event-type whitelist. Producers that pass an unknown name get logged
# and dropped (defensive — keeps the stream parseable when a refactor
# misspells the constant). Adding a new event: append here AND extend
# the docstring above so the contract stays in one place.
EVENT_TYPES: frozenset[str] = frozenset(
    {
        "overlay.matched",
        "overlay.throttled",
        "queue.enqueued",
        "queue.duplicate_skipped",
        "queue.popped",
        "task.started",
        "task.finished",
        "task.failed",
        "task.preempted",
        "approval.requested",
        "dsl.step",
    }
)


def _redis_key(instance_id: str) -> str:
    iid = str(instance_id or "").strip() or "unknown"
    return f"wos:debug:timeline:{iid}"


def _build_payload(
    *,
    event: str,
    instance_id: str,
    task_id: str | None,
    fields: dict[str, Any] | None,
) -> str | None:
    """Encode a single timeline row; returns ``None`` on validation failure."""
    ev = str(event or "").strip()
    if ev not in EVENT_TYPES:
        logger.debug("timeline: unknown event %r dropped", ev)
        return None
    body: dict[str, Any] = {
        "ts": time.time(),
        "event": ev,
        "instance_id": str(instance_id or "").strip(),
        "task_id": str(task_id or "").strip(),
    }
    if fields:
        # Caller-supplied keys override nothing — ``ts``/``event``/``instance_id``
        # /``task_id`` are protected so a stray payload can't shadow them.
        for k, v in fields.items():
            if k in ("ts", "event", "instance_id", "task_id"):
                continue
            body[k] = v
    try:
        return json.dumps(body, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        logger.debug("timeline: payload encode failed for %s", ev, exc_info=True)
        return None


async def record_event_async(
    redis_client: Any | None,
    instance_id: str,
    event: str,
    *,
    task_id: str | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    """Append one event to the per-instance timeline (async producers).

    No-op when ``redis_client`` is ``None`` (tests / standalone scripts). All
    Redis errors are swallowed at ``debug`` log level — the timeline is an
    observability aid, not a hard-state store, and must never break the
    hot path.
    """
    if redis_client is None:
        return
    encoded = _build_payload(
        event=event,
        instance_id=instance_id,
        task_id=task_id,
        fields=fields,
    )
    if encoded is None:
        return
    key = _redis_key(instance_id)
    try:
        await redis_client.lpush(key, encoded)
        await redis_client.ltrim(key, 0, MAX_TIMELINE_EVENTS - 1)
        await redis_client.expire(key, RETENTION_SECONDS)
    except Exception:
        logger.debug("timeline: async write failed for %s", event, exc_info=True)


def record_event_sync(
    redis_client: Any | None,
    instance_id: str,
    event: str,
    *,
    task_id: str | None = None,
    fields: dict[str, Any] | None = None,
) -> None:
    """Sync sibling of :func:`record_event_async`.

    Used by sync producers — most notably ``actions.tap._require_approval``,
    which blocks the worker thread on a UI decision and has no async client
    in scope.
    """
    if redis_client is None:
        return
    encoded = _build_payload(
        event=event,
        instance_id=instance_id,
        task_id=task_id,
        fields=fields,
    )
    if encoded is None:
        return
    key = _redis_key(instance_id)
    try:
        redis_client.lpush(key, encoded)
        redis_client.ltrim(key, 0, MAX_TIMELINE_EVENTS - 1)
        redis_client.expire(key, RETENTION_SECONDS)
    except Exception:
        logger.debug("timeline: sync write failed for %s", event, exc_info=True)


def read_timeline(
    redis_client: Any,
    instance_id: str,
    *,
    limit: int = 500,
    task_id: str | None = None,
    events: Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Read recent timeline rows for ``instance_id``.

    Returns rows newest-first, decoded from JSON. Filters:

    * ``task_id`` — keep only rows with a matching ``task_id``. Rows without
      a ``task_id`` (pre-task events like ``overlay.matched``) are dropped
      from this filtered view; the caller is expected to do its own join.
    * ``events`` — restrict to a subset of ``EVENT_TYPES``.

    Designed for the sync Streamlit reader; the async path uses the same
    helper via a thread bridge if it ever needs to.
    """
    key = _redis_key(instance_id)
    try:
        raw_rows = redis_client.lrange(key, 0, max(1, int(limit)) - 1) or []
    except Exception:
        logger.debug("timeline: read failed for %s", instance_id, exc_info=True)
        return []

    want_events: set[str] | None
    if events is not None:
        want_events = {str(e).strip() for e in events if str(e).strip()}
    else:
        want_events = None
    want_task = str(task_id).strip() if task_id is not None else None

    out: list[dict[str, Any]] = []
    for raw in raw_rows:
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
        try:
            row = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(row, dict):
            continue
        if want_events is not None and str(row.get("event") or "") not in want_events:
            continue
        if want_task is not None and str(row.get("task_id") or "") != want_task:
            continue
        out.append(row)
    return out
