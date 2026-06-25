"""Multi-queue build planner exec + navigate_to_building building_key routing."""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

_EXEC = Path(__file__).resolve().parents[1] / "exec.py"
_spec = importlib.util.spec_from_file_location("building_common_exec_plan", _EXEC)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(_mod)


class _FakeRedis:
    """Minimal async hash store matching the exec's hgetall/hset/hget usage."""

    def __init__(self, initial: dict | None = None) -> None:
        self.store: dict[str, str] = dict(initial or {})

    async def hgetall(self, _key: str) -> dict[str, str]:
        return dict(self.store)

    async def hset(self, _key: str, mapping: dict | None = None, **_kw) -> None:
        if mapping:
            self.store.update({k: str(v) for k, v in mapping.items()})

    async def hget(self, _key: str, field: str):
        return self.store.get(field)


def _ctx(redis, **args):
    return SimpleNamespace(redis_client=redis, instance_id="bs1", args=args, result={})


def test_handler_registered() -> None:
    assert "plan_next_builds" in _mod.DSL_EXEC_HANDLERS


def test_plan_next_builds_writes_two_distinct_picks() -> None:
    """A developed city (furnace maxed) still yields two distinct economy/camp
    picks — plan_builds keeps free queues busy where plan_next would goal_reach."""
    r = _FakeRedis({"buildings.levels.furnace": "30"})
    ctx = _ctx(r)
    asyncio.run(_mod.DSL_EXEC_HANDLERS["plan_next_builds"](ctx))

    assert ctx.result["action"] == "planned"
    assert ctx.result["reason"] == "selected"
    q1 = r.store.get("planner.build_q1")
    q2 = r.store.get("planner.build_q2")
    assert q1, "queue 1 pick written"
    assert q2, "queue 2 pick written"
    assert q1 != q2, "two free queues must pick two distinct buildings"
    assert r.store.get("planner.build_q1_to_level")
    assert ctx.result["picks"][:2] == [q1, q2]


def test_levels_from_plate_texts_parses_and_keeps_max() -> None:
    """The building-level reader's aggregation seam: parse "<Name> Lv. N" plates,
    drop unparseable ones, and never lower a slug's level on a re-sighting."""
    texts = [
        "Furnace Lv. 30",
        "Hunters' Hut Lv. 3",
        "Storehouse Lv 29",
        "Survivors are getting cold",  # no level → dropped
        "Furnace Lv. 12",             # stale re-read → must not lower 30
    ]
    got = _mod._levels_from_plate_texts(texts)
    assert got["furnace"] == 30
    assert got["hunters_hut"] == 3
    assert got["storehouse"] == 29
    assert "survivors_are_getting_cold" not in got


def test_sweep_handler_registered() -> None:
    assert "sweep_building_levels" in _mod.DSL_EXEC_HANDLERS


def test_navigate_building_key_reads_custom_field() -> None:
    """navigate_to_building honours building_key (the multi-queue picks live in
    planner.build_q1/.build_q2, not the furnace-first planner.next_building)."""
    # Empty target field → clean no_building before any map/device work.
    r = _FakeRedis({})
    ctx = _ctx(r, building_key="planner.build_q1")
    asyncio.run(_mod.DSL_EXEC_HANDLERS["navigate_to_building"](ctx))
    assert ctx.result["reason"] == "no_building"
