"""``exec: sync_hero_gear`` — OCR'd hero-gear cells → planner['hero_gear']['owned']."""
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
    assert "sync_hero_gear" in build_dsl_exec_registry()


@pytest.mark.asyncio
async def test_persists_nested_pieces() -> None:
    redis = _FakeRedis({PKEY: {
        "hero_gear.read.gloves_belt_infantry.enhance": "45",
        "hero_gear.read.gloves_belt_infantry.mastery": "12",
        "hero_gear.read.gloves_belt_infantry.widget": "5",
        "hero_gear.read.goggles_boots_lancer.enhance": "48",
    }})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_hero_gear"](ctx)

    assert ctx.result == {"action": "synced", "pieces": 2, "player_id": PLAYER}
    owned = json.loads(redis.store[PKEY]["hero_gear.owned"])
    assert owned == {
        "gloves_belt_infantry": {"enhance": 45, "mastery": 12, "widget": 5},
        "goggles_boots_lancer": {"enhance": 48},
    }
    assert json.loads(redis.store[IKEY]["hero_gear.owned"]) == owned


@pytest.mark.asyncio
async def test_noop_when_no_pieces_read() -> None:
    redis = _FakeRedis({PKEY: {"unrelated": "x"}})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_hero_gear"](ctx)

    assert ctx.result == {}
    assert "hero_gear.owned" not in redis.store.get(PKEY, {})
