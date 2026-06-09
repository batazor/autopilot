from __future__ import annotations

import json
from typing import Any

import pytest

from dashboard.load_failures import (
    LOAD_FAILURES_KEY,
    read_load_failures,
    record_load_failures,
    record_load_failures_async,
)


class _SyncHashRedis:
    """Minimal sync fake Redis: hset / hdel / hgetall on one hash."""

    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}

    def hset(self, key: str, field: str, value: str) -> int:
        self.hashes.setdefault(key, {})[field] = value
        return 1

    def hdel(self, key: str, field: str) -> int:
        return 1 if self.hashes.get(key, {}).pop(field, None) is not None else 0

    def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.hashes.get(key, {}))


class _AsyncHashRedis:
    """Async producer facade over the same in-memory store (read side stays sync)."""

    def __init__(self, store: _SyncHashRedis) -> None:
        self._store = store

    async def hset(self, key: str, field: str, value: str) -> int:
        return self._store.hset(key, field, value)

    async def hdel(self, key: str, field: str) -> int:
        return self._store.hdel(key, field)


def test_record_and_read_roundtrip_tags_source_and_sorts_newest_first() -> None:
    client = _SyncHashRedis()
    record_load_failures(
        client,
        "scenario_loader",
        [{"file": "/x/broken.yaml", "error": "boom", "ts": 100.0}],
    )
    record_load_failures(
        client,
        "evaluator",
        [{"scenario": "Regular", "task": "daily_checkin", "error": "nope", "ts": 200.0}],
    )

    out = read_load_failures(client)

    assert [e["source"] for e in out] == ["evaluator", "scenario_loader"]
    assert out[0]["scenario"] == "Regular"
    assert out[1]["file"] == "/x/broken.yaml"


def test_record_empty_clears_only_that_source() -> None:
    client = _SyncHashRedis()
    record_load_failures(client, "scenario_loader", [{"file": "a", "error": "e", "ts": 1.0}])
    record_load_failures(client, "evaluator", [{"scenario": "s", "error": "e", "ts": 2.0}])

    record_load_failures(client, "scenario_loader", [])

    out = read_load_failures(client)
    assert [e["source"] for e in out] == ["evaluator"]


def test_read_skips_unparseable_fields() -> None:
    client = _SyncHashRedis()
    client.hashes[LOAD_FAILURES_KEY] = {
        "garbage": "not json",
        "not_a_list": json.dumps({"file": "x"}),
        "ok": json.dumps([{"file": "y", "error": "e", "ts": 1.0}]),
    }

    out = read_load_failures(client)

    assert len(out) == 1
    assert out[0]["source"] == "ok"


def test_redis_errors_are_swallowed() -> None:
    msg = "down"

    class _Broken:
        def hset(self, *a: Any, **k: Any) -> None:
            raise ConnectionError(msg)

        def hdel(self, *a: Any, **k: Any) -> None:
            raise ConnectionError(msg)

        def hgetall(self, *a: Any, **k: Any) -> None:
            raise ConnectionError(msg)

    record_load_failures(_Broken(), "scenario_loader", [{"file": "a", "error": "e", "ts": 1.0}])
    record_load_failures(_Broken(), "scenario_loader", [])
    assert read_load_failures(_Broken()) == []


@pytest.mark.asyncio
async def test_async_record_matches_sync_wire_format() -> None:
    store = _SyncHashRedis()
    client = _AsyncHashRedis(store)
    await record_load_failures_async(
        client, "evaluator", [{"scenario": "s", "task": "t", "error": "e", "ts": 1.0}]
    )

    out = read_load_failures(store)
    assert out == [{"source": "evaluator", "scenario": "s", "task": "t", "error": "e", "ts": 1.0}]

    await record_load_failures_async(client, "evaluator", [])
    assert read_load_failures(store) == []
