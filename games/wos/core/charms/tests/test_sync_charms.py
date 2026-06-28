"""``exec: sync_charms`` — OCR'd charm slots → planner['charms']['owned']."""
from __future__ import annotations

import json

import pytest

from tasks.dsl_exec import DslExecContext
from tasks.dsl_exec.registry import build_dsl_exec_registry


class _FakeRedis:
    def __init__(self, seed: dict[str, dict[str, str]] | None = None) -> None:
        self.store: dict[str, dict[str, str]] = {k: dict(v) for k, v in (seed or {}).items()}

    async def hget(self, key: str, field: str) -> str | None:
        return self.store.get(key, {}).get(field)

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self.store.get(key, {}))

    async def hset(self, key: str, *, mapping: dict[str, str]) -> None:
        self.store.setdefault(key, {}).update(mapping)


PLAYER = "401227964"
PKEY = f"wos:player:{PLAYER}:state"
IKEY = "wos:instance:bs1:state"


def _ctx(redis: _FakeRedis) -> DslExecContext:
    return DslExecContext(redis_client=redis, player_id=PLAYER, instance_id="bs1", args={})


def test_handler_is_registered() -> None:
    assert "sync_charms" in build_dsl_exec_registry()


@pytest.mark.asyncio
async def test_persists_charm_slots() -> None:
    redis = _FakeRedis({PKEY: {
        "charms.read.infantry_1": "5",
        "charms.read.infantry_2": "3",
        "charms.read.marksman_6": "2",
    }})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_charms"](ctx)

    assert ctx.result == {"action": "synced", "slots": 3, "player_id": PLAYER}
    owned = json.loads(redis.store[PKEY]["charms.owned"])
    assert owned == {"infantry_1": 5, "infantry_2": 3, "marksman_6": 2}
    assert json.loads(redis.store[IKEY]["charms.owned"]) == owned


@pytest.mark.asyncio
async def test_noop_when_no_slots_read() -> None:
    redis = _FakeRedis({PKEY: {"unrelated": "x"}})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_charms"](ctx)

    assert ctx.result == {}
    assert "charms.owned" not in redis.store.get(PKEY, {})
