"""Tests for ``debug.timeline`` — bounded per-instance event log."""

from __future__ import annotations

import json

import pytest

from debug.timeline import (
    EVENT_TYPES,
    RETENTION_SECONDS,
    _redis_key,
    read_timeline,
    record_event_async,
    record_event_sync,
)


@pytest.mark.asyncio
async def test_record_event_async_writes_payload(redis_async: object) -> None:
    """Happy path: one event lands as a JSON row at the per-instance key."""
    await record_event_async(
        redis_async,
        "bs1",
        "queue.enqueued",
        task_id="t-1",
        fields={"task_type": "read_mail_gifts", "priority": 80_000},
    )
    raw_rows = await redis_async.lrange(_redis_key("bs1"), 0, -1)  # type: ignore[attr-defined]
    assert len(raw_rows) == 1
    row = json.loads(raw_rows[0])
    assert row["event"] == "queue.enqueued"
    assert row["task_id"] == "t-1"
    assert row["instance_id"] == "bs1"
    assert row["task_type"] == "read_mail_gifts"
    assert row["priority"] == 80_000
    assert isinstance(row["ts"], float)


@pytest.mark.asyncio
async def test_record_event_async_drops_unknown_event(redis_async: object) -> None:
    """Producers that pass an unknown event get silently dropped — no row written."""
    await record_event_async(
        redis_async, "bs1", "definitely.not.an.event", task_id="t-1"
    )
    assert await redis_async.llen(_redis_key("bs1")) == 0  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_record_event_async_protects_reserved_fields(redis_async: object) -> None:
    """Caller can't override ``ts``/``event``/``task_id`` via ``fields``."""
    await record_event_async(
        redis_async,
        "bs1",
        "task.started",
        task_id="t-real",
        fields={"ts": 0.0, "event": "spoofed", "task_id": "spoofed", "extra": "ok"},
    )
    raw_rows = await redis_async.lrange(_redis_key("bs1"), 0, -1)  # type: ignore[attr-defined]
    row = json.loads(raw_rows[0])
    assert row["event"] == "task.started"
    assert row["task_id"] == "t-real"
    assert row["ts"] != 0.0
    assert row["extra"] == "ok"


@pytest.mark.asyncio
async def test_record_event_async_handles_none_client() -> None:
    """No-op when no Redis is configured — must not raise."""
    await record_event_async(None, "bs1", "task.started", task_id="t-1")


@pytest.mark.asyncio
async def test_record_event_async_applies_ltrim_cap(redis_async: object) -> None:
    """LTRIM bounds the list to MAX_TIMELINE_EVENTS.

    We don't write 5000 entries (slow) — instead monkeypatch the cap so the
    test runs cheap but still exercises the trim path.
    """
    from debug import timeline as tl_mod

    original_cap = tl_mod.MAX_TIMELINE_EVENTS
    tl_mod.MAX_TIMELINE_EVENTS = 3
    try:
        for i in range(5):
            await record_event_async(
                redis_async, "bs1", "task.started", task_id=f"t-{i}"
            )
        assert await redis_async.llen(_redis_key("bs1")) == 3  # type: ignore[attr-defined]
        raw_rows = await redis_async.lrange(_redis_key("bs1"), 0, -1)  # type: ignore[attr-defined]
        kept_ids = {json.loads(r)["task_id"] for r in raw_rows}
        # Newest 3 retained, oldest 2 trimmed (LPUSH puts newest at head).
        assert kept_ids == {"t-2", "t-3", "t-4"}
    finally:
        tl_mod.MAX_TIMELINE_EVENTS = original_cap


@pytest.mark.asyncio
async def test_record_event_async_sets_expire(redis_async: object) -> None:
    """Idle TTL applied so dead instances don't leak history forever."""
    await record_event_async(redis_async, "bs1", "task.started", task_id="t-1")
    ttl = int(await redis_async.ttl(_redis_key("bs1")))  # type: ignore[attr-defined]
    # TTL was just set, so should be close to RETENTION_SECONDS.
    assert 0 < ttl <= RETENTION_SECONDS


def test_record_event_sync_writes_payload(redis_sync: object) -> None:
    """Sync sibling exists for ``actions.tap._require_approval`` and friends."""
    record_event_sync(
        redis_sync,
        "bs1",
        "approval.requested",
        task_id="t-9",
        fields={"region": "button.claim"},
    )
    raw_rows = redis_sync.lrange(_redis_key("bs1"), 0, -1)  # type: ignore[attr-defined]
    assert len(raw_rows) == 1
    row = json.loads(raw_rows[0])
    assert row["event"] == "approval.requested"
    assert row["task_id"] == "t-9"
    assert row["region"] == "button.claim"


def test_read_timeline_filters_by_task_id(redis_sync: object) -> None:
    """``read_timeline(task_id=...)`` returns only rows for one task."""
    for i in range(3):
        record_event_sync(
            redis_sync, "bs1", "task.started", task_id=f"t-{i}", fields={"i": i}
        )
    rows = read_timeline(redis_sync, "bs1", task_id="t-1")
    assert len(rows) == 1
    assert rows[0]["task_id"] == "t-1"
    assert rows[0]["i"] == 1


def test_read_timeline_filters_by_event(redis_sync: object) -> None:
    """``read_timeline(events=...)`` restricts to a subset of EVENT_TYPES."""
    record_event_sync(redis_sync, "bs1", "task.started", task_id="t-1")
    record_event_sync(redis_sync, "bs1", "task.finished", task_id="t-1")
    record_event_sync(redis_sync, "bs1", "queue.enqueued", task_id="t-1")
    rows = read_timeline(
        redis_sync, "bs1", events=["task.started", "task.finished"]
    )
    assert {r["event"] for r in rows} == {"task.started", "task.finished"}


def test_read_timeline_returns_newest_first(redis_sync: object) -> None:
    """LPUSH-based log: index 0 = newest, matches notifications/history style."""
    for i in range(3):
        record_event_sync(
            redis_sync, "bs1", "task.started", task_id=f"t-{i}"
        )
    rows = read_timeline(redis_sync, "bs1", limit=10)
    assert [r["task_id"] for r in rows] == ["t-2", "t-1", "t-0"]


def test_event_types_constant_covers_documented_set() -> None:
    """The whitelist contract — adding a producer requires updating EVENT_TYPES."""
    assert EVENT_TYPES >= {
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
