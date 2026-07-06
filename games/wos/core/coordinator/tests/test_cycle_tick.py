"""Autonomous cycle tick: state → plan_cycle → trace (→ dispatch when enabled)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from games.wos.core.coordinator.cycle_tick import (
    CycleConfig,
    collect_cycle_inputs,
    cycle_trace_key,
    run_cycle_tick,
)


class _FakePipe:
    def __init__(self, redis: _FakeRedis) -> None:
        self._redis = redis

    def zadd(self, key: str, mapping: dict) -> None:
        self._redis.zsets.setdefault(key, {}).update(mapping)

    def zremrangebyscore(self, *a: Any) -> None: ...
    def zremrangebyrank(self, *a: Any) -> None: ...
    def expire(self, *a: Any) -> None: ...

    async def execute(self) -> None: ...


class _FakeRedis:
    def __init__(self) -> None:
        self.zsets: dict[str, dict[str, float]] = {}

    def pipeline(self, transaction: bool = False) -> _FakePipe:
        return _FakePipe(self)


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


_STATE = {
    # Enough of a base for the furnace-first ladder to want SOMETHING.
    "buildings.levels.furnace": "10",
    "buildings.levels.embassy": "8",
    "buildings.levels.infirmary": "7",
    # Noise that the prefix parser must ignore.
    "buildings.levels.furnace_at": "1783260000.0",
    "buildings.levels.bogus": "not-a-number",
    "stamina": "100",
}


def test_collect_inputs_builds_slate_from_state() -> None:
    inputs = collect_cycle_inputs(_STATE)
    assert "build_slate" in inputs and "build_graph" in inputs
    # No research.levels.* in state → the domain degrades to absent, not garbage.
    assert "research_plan" not in inputs


def test_collect_inputs_blind_player_is_empty() -> None:
    assert collect_cycle_inputs({"stamina": "100"}) == {}


def test_tick_traces_decision_and_respects_interval() -> None:
    redis, queue = _FakeRedis(), _FakeQueue()
    cfg = CycleConfig(enabled=True, interval_s=600.0, dispatch=False)

    async def run(now: float):
        return await run_cycle_tick(
            redis=redis, queue=queue, instance_id="bs6", player_id="cycle_p1",
            state=_STATE, now=now, config=cfg,
        )

    plan = asyncio.run(run(1000.0))
    assert plan is not None
    rows = redis.zsets.get(cycle_trace_key("cycle_p1"), {})
    assert len(rows) == 1
    payload = json.loads(next(iter(rows)))
    assert payload["action"] in ("plan", "idle")
    assert payload["dispatched"] == []            # trace-only shipping default
    assert queue.calls == []

    # Second tick inside the interval is throttled per player.
    assert asyncio.run(run(1100.0)) is None


def test_tick_dispatches_when_enabled() -> None:
    redis, queue = _FakeRedis(), _FakeQueue()
    cfg = CycleConfig(enabled=True, interval_s=0.0, dispatch=True)

    async def run():
        return await run_cycle_tick(
            redis=redis, queue=queue, instance_id="bs6", player_id="cycle_p2",
            state=_STATE, now=2000.0, config=cfg,
        )

    plan = asyncio.run(run())
    assert plan is not None
    if plan.decision.commits:  # ladder found an affordable pick on this state
        assert queue.calls, "commits present but nothing dispatched"
        assert all(c["skip_if_duplicate"] for c in queue.calls)
        assert {c["task_type"] for c in queue.calls} <= {
            "building.plan_tick.cron",
            "research.plan_tick.cron",
        }


def test_tick_disabled_is_noop() -> None:
    redis, queue = _FakeRedis(), _FakeQueue()
    cfg = CycleConfig(enabled=False)

    async def run():
        return await run_cycle_tick(
            redis=redis, queue=queue, instance_id="bs6", player_id="cycle_p3",
            state=_STATE, now=3000.0, config=cfg,
        )

    assert asyncio.run(run()) is None
    assert redis.zsets == {} and queue.calls == []
