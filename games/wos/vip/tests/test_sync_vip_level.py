"""``exec: sync_vip_level`` — parse the OCR'd VIP level into Redis + state store."""
from __future__ import annotations

import pytest

from tasks.dsl_exec import DslExecContext
from tasks.dsl_exec.registry import build_dsl_exec_registry


class _FakeRedis:
    """Records hset mappings; serves seeded hget fields per key."""

    def __init__(self, seed: dict[str, dict[str, str]] | None = None) -> None:
        self.store: dict[str, dict[str, str]] = {k: dict(v) for k, v in (seed or {}).items()}

    async def hget(self, key: str, field: str) -> str | None:
        return self.store.get(key, {}).get(field)

    async def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self.store.setdefault(key, {}).update(mapping)


PLAYER = "401227964"
PKEY = f"wos:player:{PLAYER}:state"
IKEY = "wos:instance:bs1:state"


def _ctx(redis: _FakeRedis) -> DslExecContext:
    return DslExecContext(redis_client=redis, player_id=PLAYER, instance_id="bs1", args={})


def test_handler_is_registered():
    assert "sync_vip_level" in build_dsl_exec_registry()


@pytest.mark.asyncio
async def test_persists_parsed_level():
    redis = _FakeRedis({PKEY: {"vip.level": "8"}})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_vip_level"](ctx)

    assert ctx.result == {"action": "synced", "level": 8, "player_id": PLAYER}
    assert redis.store[PKEY]["vip.level"] == "8"
    assert redis.store[IKEY]["vip.level"] == "8"          # mirrored to the instance hash
    assert "vip.level.synced_at" in redis.store[PKEY]


@pytest.mark.asyncio
async def test_unparseable_level_is_skipped():
    redis = _FakeRedis({PKEY: {"vip.level": "??"}})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_vip_level"](ctx)

    assert ctx.result == {}
    assert "vip.level.synced_at" not in redis.store.get(IKEY, {})


@pytest.mark.asyncio
async def test_negative_level_is_skipped():
    redis = _FakeRedis({PKEY: {"vip.level": "-3"}})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_vip_level"](ctx)

    assert ctx.result == {}


@pytest.mark.asyncio
async def test_reads_from_instance_hash_fallback():
    # OCR step stored on the instance hash (device-level path) → still picked up.
    redis = _FakeRedis({IKEY: {"vip.level": "11"}})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_vip_level"](ctx)

    assert ctx.result["level"] == 11
