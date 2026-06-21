"""Tests for the Redis-backed calendar adapter (pure helpers + async fakes)."""
from __future__ import annotations

from datetime import UTC, datetime

from games.wos.core.calendar import adapter, schedule

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=UTC).timestamp()


def _events():
    return [
        ("Live", datetime(2026, 6, 15, tzinfo=UTC), datetime(2026, 6, 16, tzinfo=UTC)),
        ("Soon", datetime(2026, 6, 16, 8, tzinfo=UTC), datetime(2026, 6, 16, 10, tzinfo=UTC)),
    ]


def _view():
    return schedule.build_view(_events(), datetime.fromtimestamp(NOW, tz=UTC), days=3)


class _FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.kv: dict[str, str] = {}

    async def hset(self, key, *, mapping):
        self.hashes.setdefault(key, {}).update({str(k): str(v) for k, v in mapping.items()})
        return len(mapping)

    async def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = str(value)
        return True


def test_should_refresh_on_ttl():
    assert adapter.should_refresh(None, NOW) is True
    assert adapter.should_refresh(NOW, NOW, ttl=3600) is False
    assert adapter.should_refresh(NOW - 7200, NOW, ttl=3600) is True


def test_shared_mapping_decode_round_trip():
    mapping = adapter.shared_mapping(_view(), NOW, source="sqlite")
    decoded = adapter.decode_shared(mapping)
    assert decoded["read_at"] == NOW
    assert decoded["source"] == "sqlite"
    assert decoded["flags"] == {"event_live": 1, "event_soon": 0}
    # bytes hash (as redis returns) decodes too
    raw_bytes = {k.encode(): v.encode() for k, v in mapping.items()}
    assert adapter.decode_shared(raw_bytes)["read_at"] == NOW


def test_derive_flags_recomputed_from_digest():
    shared = {"digest": _view()["digest"]}
    assert adapter.derive_flags(shared, NOW)["event_live"] == 1
    later = datetime(2026, 6, 17, tzinfo=UTC).timestamp()       # both windows closed
    assert adapter.derive_flags(shared, later) == {"event_live": 0, "event_soon": 0}


async def test_write_then_read_shared_round_trips():
    redis = _FakeRedis()
    await adapter.write_shared(redis, "1234", _view(), NOW)
    assert "wos:state:1234:calendar" in redis.hashes
    shared = await adapter.read_shared(redis, "1234")
    assert shared["read_at"] == NOW
    assert adapter.derive_flags(shared, NOW)["event_live"] == 1


async def test_refresh_lock_is_single_winner():
    redis = _FakeRedis()
    assert await adapter.acquire_refresh_lock(redis, "1234") is True
    assert await adapter.acquire_refresh_lock(redis, "1234") is False


async def test_apply_flags_writes_player_state():
    redis = _FakeRedis()
    await adapter.apply_flags_to_player(redis, "42", {"event_live": 1, "event_soon": 0})
    assert redis.hashes["wos:player:42:state"] == {"event_live": "1", "event_soon": "0"}


async def test_apply_flags_noop_when_empty():
    redis = _FakeRedis()
    await adapter.apply_flags_to_player(redis, "42", {})
    assert redis.hashes == {}
