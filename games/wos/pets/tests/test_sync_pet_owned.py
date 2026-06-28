"""``exec: sync_pet_owned`` — OCR'd pet cells → planner['pets']['owned']."""
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
    assert "sync_pet_owned" in build_dsl_exec_registry()


@pytest.mark.asyncio
async def test_persists_owned_roster() -> None:
    redis = _FakeRedis({PKEY: {
        "pets.read.snow_leopard.level": "30",
        "pets.read.snow_leopard.refine": "5",
        "pets.read.snow_leopard.skill": "3",
        "pets.read.giant_elk.level": "20",
    }})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_pet_owned"](ctx)

    assert ctx.result["action"] == "synced"
    assert ctx.result["pets"] == 2
    owned = json.loads(redis.store[PKEY]["pets.owned"])
    assert owned == {
        "snow_leopard": {"level": 30, "refine": 5, "skill": 3},
        "giant_elk": {"level": 20},
    }
    assert json.loads(redis.store[IKEY]["pets.owned"]) == owned  # instance mirror


@pytest.mark.asyncio
async def test_noop_when_no_cells_read() -> None:
    redis = _FakeRedis({PKEY: {"unrelated": "x"}})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_pet_owned"](ctx)

    assert ctx.result == {}                       # nothing read → clean no-op
    assert "pets.owned" not in redis.store.get(PKEY, {})
