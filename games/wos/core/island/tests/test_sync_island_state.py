"""``exec: sync_island_state`` — OCR'd island stats → planner['island']['owned']."""
from __future__ import annotations

import json

import pytest
from games.wos.core.island.exec import parse_island_state

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


# --- pure parse ------------------------------------------------------------- #
def test_parse_island_state_shapes_scalars_decorations_lumber() -> None:
    fields = {
        "island.read.tree_of_life_level": "Lv. 5",
        "island.read.prosperity": "1200",
        "island.read.life_essence": "8500",
        "island.read.decoration.silk_banner": "3",
        "island.read.decoration.golden_lantern": "2",
        "island.read.lumber.1": "4",
        "island.read.lumber.0": "3",
    }
    assert parse_island_state(fields) == {
        "tree_of_life_level": 5,
        "prosperity": 1200,
        "life_essence": 8500,
        "decorations": {"silk_banner": 3, "golden_lantern": 2},
        "lumber_camp_levels": [3, 4],   # ordered by key (0, 1)
    }


# --- handler ---------------------------------------------------------------- #
def test_handler_is_registered() -> None:
    assert "sync_island_state" in build_dsl_exec_registry()


@pytest.mark.asyncio
async def test_persists_island_state_and_flat_mirror() -> None:
    redis = _FakeRedis({PKEY: {
        "island.read.tree_of_life_level": "5",
        "island.read.prosperity": "1200",
    }})
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_island_state"](ctx)

    assert ctx.result["action"] == "synced"
    assert ctx.result["tree_of_life_level"] == 5
    # planner['island']['owned'] mirror
    assert json.loads(redis.store[PKEY]["island.owned"]) == {
        "tree_of_life_level": 5, "prosperity": 1200,
    }
    # the observed_input flat key (what `botctl planners` blind-checks)
    assert redis.store[PKEY]["island.tree_of_life.level"] == "5"
    assert redis.store[IKEY]["island.tree_of_life.level"] == "5"


@pytest.mark.asyncio
async def test_noop_without_tree_level() -> None:
    redis = _FakeRedis({PKEY: {"island.read.prosperity": "1200"}})  # no tree level
    ctx = _ctx(redis)

    await build_dsl_exec_registry()["sync_island_state"](ctx)

    assert ctx.result == {}
    assert "island.owned" not in redis.store.get(PKEY, {})
