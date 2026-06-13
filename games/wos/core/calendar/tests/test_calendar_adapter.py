"""Tests for the Redis-backed calendar adapter.

``build_view`` / ``state_mapping`` are exercised purely; ``publish`` uses a
minimal async Redis fake — no real Redis, no ADB.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

from games.wos.core.calendar import adapter
from games.wos.core.calendar.model import Calendar

# 2026-06-15 12:00 UTC as a unix ts (matches test_model's NOW).
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC).timestamp()
TODAY_WD = datetime.fromtimestamp(NOW, tz=UTC).weekday()


def _weekday_name(wd: int) -> str:
    return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][wd]


def _calendar() -> Calendar:
    return Calendar.from_dict({"events": [
        {"id": "live", "title": "Live", "recurrence": "daily",
         "start": "00:00", "end": "24:00", "state_flag": "event_live",
         "scenario": "do_live"},
        {"id": "soon", "title": "Soon", "recurrence": "weekly",
         "weekdays": [_weekday_name((TODAY_WD + 1) % 7)], "start": "08:00",
         "end": "10:00", "state_flag": "event_soon"},
    ]})


class _FakeRedis:
    def __init__(self) -> None:
        self.hset_calls: list[tuple[str, dict]] = []

    async def hset(self, key, *, mapping):
        self.hset_calls.append((key, mapping))
        return len(mapping)


def test_build_view_separates_active_and_upcoming():
    view = adapter.build_view(_calendar(), NOW, days=3)
    assert [e["id"] for e in view["active"]] == ["live"]
    assert [e["id"] for e in view["upcoming"]] == ["soon"]
    assert view["flags"] == {"event_live": 1, "event_soon": 0}
    assert len(view["digest"]) == 3
    # Upcoming carries a positive lead time.
    assert view["upcoming"][0]["in_hours"] > 0


def test_state_mapping_flattens_flags_and_json_blobs():
    view = adapter.build_view(_calendar(), NOW, days=3)
    mapping = adapter.state_mapping(view, NOW)
    assert mapping["event_live"] == "1"
    assert mapping["event_soon"] == "0"
    assert mapping["calendar_at"] == str(NOW)
    # Blobs round-trip as JSON.
    assert [e["id"] for e in json.loads(mapping["calendar_upcoming"])] == ["soon"]
    assert len(json.loads(mapping["calendar_digest"])) == 3


async def test_publish_writes_player_state():
    redis = _FakeRedis()
    view = await adapter.publish(redis, "42", _calendar(), NOW, days=3)
    assert len(redis.hset_calls) == 1
    key, mapping = redis.hset_calls[0]
    assert key == "wos:player:42:state"
    assert mapping["event_live"] == "1"
    assert view["flags"]["event_live"] == 1


async def test_publish_noop_without_target():
    # No player id → compute the view but skip the write (no crash).
    view = await adapter.publish(None, "", _calendar(), NOW)
    assert view["flags"]["event_live"] == 1
