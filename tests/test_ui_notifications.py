from __future__ import annotations

import json
from typing import Any

import pytest

from ui.notifications import (
    MAX_RETAINED_NOTIFICATIONS,
    pop_new_notifications,
    push_ui_notification,
)


class _AsyncListRedis:
    """Minimal async fake Redis: lpush / ltrim / expire."""

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}
        self.expires: dict[str, int] = {}

    async def lpush(self, key: str, value: str) -> int:
        bucket = self.lists.setdefault(key, [])
        bucket.insert(0, value)
        return len(bucket)

    async def ltrim(self, key: str, start: int, stop: int) -> str:
        bucket = self.lists.get(key, [])
        if not bucket:
            return "OK"
        n = len(bucket)
        s = max(0, start) if start >= 0 else max(0, n + start)
        e = stop if stop >= 0 else n + stop
        e = min(n - 1, e)
        if s > e:
            self.lists[key] = []
        else:
            self.lists[key] = bucket[s : e + 1]
        return "OK"

    async def expire(self, key: str, seconds: int) -> int:
        self.expires[key] = int(seconds)
        return 1


class _SyncListRedis:
    """Sync facade over the same in-memory store (UI consumer side)."""

    def __init__(self, source: _AsyncListRedis) -> None:
        self._source = source

    def lrange(self, key: str, start: int, stop: int) -> list[Any]:
        bucket = self._source.lists.get(key, [])
        if not bucket:
            return []
        n = len(bucket)
        s = max(0, start) if start >= 0 else max(0, n + start)
        e = stop if stop >= 0 else n + stop
        e = min(n - 1, e)
        if s > e:
            return []
        return list(bucket[s : e + 1])


@pytest.mark.asyncio
async def test_push_and_pop_round_trip_returns_unseen_event_in_chronological_order() -> None:
    rds = _AsyncListRedis()
    sync = _SyncListRedis(rds)
    seen: set[str] = set()

    eid_a = await push_ui_notification(
        rds, "bs1", kind="exec.fetch_player", message="A", level="success"
    )
    eid_b = await push_ui_notification(
        rds, "bs1", kind="exec.fetch_player", message="B", level="success"
    )
    assert eid_a and eid_b and eid_a != eid_b

    events = pop_new_notifications(sync, "bs1", seen=seen)
    assert [e["message"] for e in events] == ["A", "B"]
    assert all(e["kind"] == "exec.fetch_player" for e in events)
    assert all(e["level"] == "success" for e in events)
    assert {e["id"] for e in events} == {eid_a, eid_b}

    seen.update(e["id"] for e in events)
    again = pop_new_notifications(sync, "bs1", seen=seen)
    assert again == [], "already-seen events must not surface again"


@pytest.mark.asyncio
async def test_pop_filters_out_stale_events_by_max_age() -> None:
    rds = _AsyncListRedis()
    sync = _SyncListRedis(rds)

    eid = await push_ui_notification(
        rds, "bs1", kind="exec.fetch_player", message="old", level="success"
    )
    assert eid is not None

    # Simulate ageing: rewrite the stored body with a stale ts.
    raw = rds.lists["wos:ui:notifications:bs1"][0]
    obj = json.loads(raw)
    obj["ts"] = obj["ts"] - 9999.0
    rds.lists["wos:ui:notifications:bs1"][0] = json.dumps(obj)

    out = pop_new_notifications(sync, "bs1", seen=set(), max_age_seconds=30.0)
    assert out == []


@pytest.mark.asyncio
async def test_push_caps_retention_to_max_retained() -> None:
    rds = _AsyncListRedis()
    for i in range(MAX_RETAINED_NOTIFICATIONS + 5):
        await push_ui_notification(
            rds, "bs1", kind="exec.test", message=f"m{i}", level="info"
        )
    assert len(rds.lists["wos:ui:notifications:bs1"]) == MAX_RETAINED_NOTIFICATIONS
    assert rds.expires.get("wos:ui:notifications:bs1") is not None


@pytest.mark.asyncio
async def test_push_is_no_op_without_redis_or_instance() -> None:
    assert await push_ui_notification(None, "bs1", kind="x", message="y") is None
    rds = _AsyncListRedis()
    assert await push_ui_notification(rds, "", kind="x", message="y") is None
    assert rds.lists == {}
