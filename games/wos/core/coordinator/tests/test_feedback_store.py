"""feedback_store: Redis persistence round-trip + the worker→march feedback loop."""
from __future__ import annotations

import asyncio

from games.wos.core.coordinator.feedback import Outcome
from games.wos.core.coordinator.feedback_store import (
    feedback_key,
    load_feedback,
    record_outcome,
)


class _FakePipe:
    def __init__(self, redis: _FakeHashRedis) -> None:
        self._redis = redis
        self._ops: list[tuple] = []

    def hset(self, key: str, field: str, value: str) -> None:
        self._ops.append(("hset", key, field, value))

    def expire(self, key: str, ttl: int) -> None:
        self._ops.append(("expire", key, ttl))

    async def execute(self) -> None:
        for op in self._ops:
            if op[0] == "hset":
                self._redis.hashes.setdefault(op[1], {})[op[2]] = op[3]


class _FakeHashRedis:
    """Just enough of redis.asyncio for the store: hget/hgetall/pipeline."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    async def hget(self, key: str, field: str) -> str | None:
        return self.hashes.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)


def test_record_and_load_roundtrip():
    redis = _FakeHashRedis()

    async def run():
        await record_outcome(redis, "p1", Outcome("intel:run", "intel", False, 1.0, reason="nav_error"))
        await record_outcome(redis, "p1", Outcome("intel:run", "intel", False, 2.0, reason="nav_error"))
        await record_outcome(redis, "p1", Outcome("gather:meat", "gather", True, 3.0))
        return await load_feedback(redis, "p1")

    state = asyncio.run(run())
    intel = state.stats["intel:run"]
    assert intel.attempts == 2
    assert intel.consecutive_stalls == 2
    assert intel.same_reason_streak == 2
    assert intel.last_reason == "nav_error"
    gather = state.stats["gather:meat"]
    assert gather.progressed == 1
    assert gather.consecutive_stalls == 0


def test_players_are_isolated():
    redis = _FakeHashRedis()

    async def run():
        await record_outcome(redis, "p1", Outcome("intel:run", "intel", False, 1.0, reason="nav_error"))
        return await load_feedback(redis, "p2")

    assert asyncio.run(run()).stats == {}
    assert feedback_key("p1") != feedback_key("p2")


def test_load_tolerates_garbage_fields():
    redis = _FakeHashRedis()
    redis.hashes[feedback_key("p1")] = {
        "ok": '{"domain":"intel","attempts":1,"progressed":0,"consecutive_stalls":1,'
        '"last_ts":5.0,"last_reason":"timeout","same_reason_streak":1}',
        "not_json": "{{{",
        "not_a_dict": "42",
    }
    state = asyncio.run(load_feedback(redis, "p1"))
    assert set(state.stats) == {"ok"}
    assert state.stats["ok"].last_reason == "timeout"


def test_redis_failure_degrades_to_empty_state():
    err = "down"

    class _Boom:
        async def hgetall(self, key: str):
            raise ConnectionError(err)

        async def hget(self, key: str, field: str):
            raise ConnectionError(err)

        def pipeline(self, transaction: bool = False):
            raise ConnectionError(err)

    async def run():
        # record must swallow, load must return the empty state
        await record_outcome(_Boom(), "p1", Outcome("intel:run", "intel", True, 1.0))
        return await load_feedback(_Boom(), "p1")

    assert asyncio.run(run()).stats == {}
