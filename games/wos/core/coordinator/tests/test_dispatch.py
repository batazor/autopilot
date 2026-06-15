"""MARCH dispatch: turn coordinate() commits into queued scenarios."""
from __future__ import annotations

from typing import Any

import pytest
from games.wos.core.coordinator import MARCH, CandidateAction, Commit, CoordinatorDecision
from games.wos.core.coordinator.dispatch import (
    DISPATCH_PRIORITY_BASE,
    MarchConfig,
    MarchScenario,
    dispatch_march,
    load_march_config,
    run_march_tick,
)


class _FakeQueue:
    """Captures schedule() calls; returns per-task_type enqueue results."""

    def __init__(
        self,
        results: dict[str, bool] | None = None,
        last_run: float | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._results = results or {}
        self._last_run = last_run

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return self._results.get(kwargs.get("task_type"), True)

    async def last_run_at(self, *, instance_id: str, task_type: str, player_id: str) -> float | None:
        return self._last_run


class _FakeRedis:
    def __init__(self, state: dict[str, str] | None = None) -> None:
        self._state = state or {}
        self.hgetall_calls = 0

    async def hgetall(self, key: str) -> dict[str, str]:
        self.hgetall_calls += 1
        return dict(self._state)


def _march_action(domain: str, *, priority: float, cost: dict[str, int] | None = None):
    return CandidateAction(
        domain=domain,
        channel_kind=MARCH,
        key=f"{domain}:k",
        priority=priority,
        cost=cost or {},
    )


def _decision(*commits: Commit) -> CoordinatorDecision:
    return CoordinatorDecision(
        commits=tuple(commits),
        starved=(),
        no_channel=(),
        remaining={},
        bottleneck_resources=(),
    )


@pytest.mark.asyncio
async def test_dispatches_committed_intel_as_intel_run():
    queue = _FakeQueue()
    decision = _decision(Commit("march_1", _march_action("intel", priority=760.0, cost={"stamina": 10})))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )

    assert len(queue.calls) == 1
    call = queue.calls[0]
    assert call["task_type"] == "intel_run"
    assert call["dsl_scenario"] == "intel_run"
    assert call["player_id"] == "p1"
    assert call["instance_id"] == "i1"
    assert call["skip_if_duplicate"] is True
    # Cross-domain priority lifted into the queue's absolute band.
    assert call["priority"] == DISPATCH_PRIORITY_BASE + 760
    assert [e.task_type for e in result.enqueued] == ["intel_run"]
    assert result.skipped == ()


@pytest.mark.asyncio
async def test_domain_without_scenario_is_skipped_not_dispatched():
    queue = _FakeQueue()
    # gather has no scenario in the default registry yet.
    decision = _decision(Commit("march_1", _march_action("gather", priority=720.0)))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )

    assert queue.calls == []
    assert result.enqueued == ()
    assert [(s.domain, s.reason) for s in result.skipped] == [("gather", "no_scenario")]


@pytest.mark.asyncio
async def test_duplicate_in_flight_is_reported_skipped():
    queue = _FakeQueue(results={"intel_run": False})  # already queued
    decision = _decision(Commit("march_1", _march_action("intel", priority=760.0)))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )

    assert len(queue.calls) == 1
    assert result.enqueued == ()
    assert [(s.domain, s.reason) for s in result.skipped] == [("intel", "duplicate")]


@pytest.mark.asyncio
async def test_no_commits_schedules_nothing():
    queue = _FakeQueue()
    result = await dispatch_march(
        _decision(), queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )
    assert queue.calls == []
    assert result.enqueued == ()
    assert result.skipped == ()


@pytest.mark.asyncio
async def test_custom_registry_dispatches_gather():
    queue = _FakeQueue()
    registry = {"gather": MarchScenario(task_type="gather_run", dsl_scenario="gather_run")}
    decision = _decision(Commit("march_2", _march_action("gather", priority=720.0)))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=2000.0,
        scenarios=registry,
    )

    assert queue.calls[0]["task_type"] == "gather_run"
    assert queue.calls[0]["priority"] == DISPATCH_PRIORITY_BASE + 720
    assert [e.task_type for e in result.enqueued] == ["gather_run"]


# --- run_march_tick: the dispatch-blind orchestration ------------------------


@pytest.mark.asyncio
async def test_tick_dispatches_intel_when_stamina_and_cooldown_ok():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis({"stamina": "100"})

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert [e.task_type for e in result.enqueued] == ["intel_run"]
    assert queue.calls[0]["task_type"] == "intel_run"


@pytest.mark.asyncio
async def test_tick_skips_during_cooldown():
    queue = _FakeQueue(last_run=9_900.0)  # 100s ago, under the 900s cooldown
    redis = _FakeRedis({"stamina": "100"})

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert result.enqueued == ()
    assert queue.calls == []


@pytest.mark.asyncio
async def test_tick_skips_without_stamina_reading():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis({})  # no stamina yet → don't act blind

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert result.enqueued == ()
    assert queue.calls == []


@pytest.mark.asyncio
async def test_tick_holds_during_joe_reserve():
    # Calendar says Joe is live → reserve 50; 56 − 50 = 6 < 10 → no intel dispatch.
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis({"stamina": "56", "joe_event_active": "1"})

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert result.enqueued == ()


@pytest.mark.asyncio
async def test_tick_no_idle_slots_dispatches_nothing():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis({"stamina": "100"})

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=0,
    )

    assert result.enqueued == ()


# --- Romance Season: a second time-limited MARCH-spending event --------------


def _romance_state(*, ttl: str, attempts: str, **extra: str) -> dict[str, str]:
    return {
        "events.romanceSeason.ttl_remaining_s": ttl,
        "events.romanceSeason.attack_count": attempts,
        **extra,
    }


@pytest.mark.asyncio
async def test_dispatches_committed_romance_as_event_scenario():
    queue = _FakeQueue()
    decision = _decision(Commit("march_1", _march_action("romance_season", priority=750.0)))

    result = await dispatch_march(
        decision, queue=queue, instance_id="i1", player_id="p1", now=1000.0
    )

    assert queue.calls[0]["task_type"] == "event.romance_season"
    assert [e.task_type for e in result.enqueued] == ["event.romance_season"]


@pytest.mark.asyncio
async def test_tick_dispatches_romance_when_active_with_attempts():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis(_romance_state(ttl="3600", attempts="5"))  # no stamina → isolate romance

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert [e.task_type for e in result.enqueued] == ["event.romance_season"]


@pytest.mark.asyncio
async def test_tick_skips_romance_when_window_expired():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis(_romance_state(ttl="0", attempts="5"))

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert result.enqueued == ()


@pytest.mark.asyncio
async def test_tick_skips_romance_when_attempts_exhausted():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis(_romance_state(ttl="3600", attempts="0"))

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert result.enqueued == ()


@pytest.mark.asyncio
async def test_tick_intel_outranks_romance_for_one_slot():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis(_romance_state(ttl="3600", attempts="5", stamina="100"))

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1,
    )

    assert [e.task_type for e in result.enqueued] == ["intel_run"]


@pytest.mark.asyncio
async def test_tick_two_slots_run_intel_and_romance():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis(_romance_state(ttl="3600", attempts="5", stamina="100"))

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=2,
    )

    assert sorted(e.task_type for e in result.enqueued) == ["event.romance_season", "intel_run"]


# --- config + injected-state plumbing ----------------------------------------


def test_load_march_config_dormant_by_default():
    cfg = load_march_config()
    assert isinstance(cfg, MarchConfig)
    assert cfg.enabled is False           # ships off; flip march.yaml to go live
    assert cfg.intel_cooldown_s == 900


@pytest.mark.asyncio
async def test_run_march_tick_uses_injected_state_without_reading_redis():
    queue = _FakeQueue(last_run=None)
    redis = _FakeRedis({})  # empty: if read, stamina is unknown → no intel dispatch

    result = await run_march_tick(
        queue=queue, redis=redis, instance_id="i1", player_id="p1",
        now=10_000.0, idle_slots=1, state={"stamina": "100"},
    )

    # Dispatched from the injected state, and the redis hash was never read.
    assert [e.task_type for e in result.enqueued] == ["intel_run"]
    assert redis.hgetall_calls == 0
