"""Cooperative preemption tests (ADR 0001 §"Cooperative preemption fixtures").

Targets ``DslScenarioTask._preempted_by_higher_priority`` directly. Uses the
testcontainer Redis for the yield-counter state (the only Redis surface the
preempt path actually touches once ``peek_top_due`` and ``_read_current_screen``
are stubbed out) so the GET / INCR / EXPIRE round-trip and TTL semantics are
exercised end-to-end instead of mocked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from scheduler.queue import QueueItem
from tasks import dsl_scenario_helpers as dsl_helpers
from tasks.dsl_scenario import (
    PREEMPT_MARGIN,
    PREEMPT_MAX_YIELDS,
    DslScenarioTask,
)
from tasks.dsl_scenario_preempt_mixin import (
    PREEMPT_YIELD_COUNT_TTL_SECONDS,
    _yield_count_key,
)

if TYPE_CHECKING:
    import redis.asyncio as aioredis


def _make_task(
    *,
    effective_priority: int,
    redis_client: aioredis.Redis,
    task_id: str = "running-1",
) -> DslScenarioTask:
    return DslScenarioTask(
        task_id=task_id,
        player_id="p1",
        priority=80_000,
        scenario_key="long_running_scenario",
        redis_client=redis_client,
        effective_priority=effective_priority,
    )


def _patch_peek_top(monkeypatch: pytest.MonkeyPatch, top: QueueItem | None) -> None:
    async def fake_peek(self, instance_id, *, current_screen=""):
        return top

    monkeypatch.setattr(
        "scheduler.queue.RedisQueue.peek_top_due", fake_peek, raising=True
    )


def _patch_read_current_screen(
    monkeypatch: pytest.MonkeyPatch, screen: str = "main_city"
) -> None:
    async def fake(instance_id, redis_client):
        return screen

    monkeypatch.setattr(dsl_helpers, "_read_current_screen", fake, raising=True)


def _top(*, task_type: str, effective_priority: int, task_id: str = "top-1") -> QueueItem:
    return QueueItem(
        task_id=task_id,
        player_id="p1",
        task_type=task_type,
        priority=effective_priority,
        run_at=0.0,
        instance_id="bs1",
        effective_priority=effective_priority,
    )


@pytest.mark.asyncio
async def test_no_yield_when_gap_below_margin(
    monkeypatch: pytest.MonkeyPatch, redis_async: aioredis.Redis
) -> None:
    """§8: running=80_000, top=83_000 → gap 3_000 < margin 5_000 → no yield."""
    _patch_read_current_screen(monkeypatch)
    _patch_peek_top(
        monkeypatch, _top(task_type="other", effective_priority=83_000)
    )
    task = _make_task(effective_priority=80_000, redis_client=redis_async)

    result = await task._preempted_by_higher_priority("bs1", step_index=5)
    assert result is None
    # yield_count must NOT be incremented on a non-yield.
    assert await redis_async.get(_yield_count_key("bs1", "running-1")) is None


@pytest.mark.asyncio
async def test_yield_when_gap_meets_margin(
    monkeypatch: pytest.MonkeyPatch, redis_async: aioredis.Redis
) -> None:
    """§9: running=83_000, top=88_000 → gap 5_000 ≥ margin → yield."""
    _patch_read_current_screen(monkeypatch)
    _patch_peek_top(
        monkeypatch, _top(task_type="tap_confirm_button", effective_priority=88_000)
    )
    task = _make_task(effective_priority=83_000, redis_client=redis_async)

    result = await task._preempted_by_higher_priority("bs1", step_index=7)
    assert result is not None
    assert result.success is False
    assert result.next_run_at is not None  # immediate re-enqueue
    md = result.metadata or {}
    assert md["reason"] == "preempted_by_higher_priority"
    assert md["preempted_by"] == "tap_confirm_button"
    assert md["preempted_by_priority"] == 88_000
    assert md["running_effective_priority"] == 83_000
    assert md["yielded_at_step"] == 7
    assert md["yield_count"] == 1
    # yield_count persisted with TTL.
    key = _yield_count_key("bs1", "running-1")
    assert await redis_async.get(key) == "1"
    ttl = await redis_async.ttl(key)
    assert 0 < ttl <= PREEMPT_YIELD_COUNT_TTL_SECONDS


@pytest.mark.asyncio
async def test_anti_starvation_immunity_after_three_yields(
    monkeypatch: pytest.MonkeyPatch, redis_async: aioredis.Redis
) -> None:
    """§10: yield_count ≥ 3 → running task is immune, does NOT yield even when
    a higher-priority pending task is waiting. The counter must not increment
    further."""
    key = _yield_count_key("bs1", "running-1")
    await redis_async.set(key, "3")

    _patch_read_current_screen(monkeypatch)
    _patch_peek_top(
        monkeypatch, _top(task_type="banner_dismiss", effective_priority=99_000)
    )
    task = _make_task(effective_priority=80_000, redis_client=redis_async)

    result = await task._preempted_by_higher_priority("bs1", step_index=12)
    assert result is None, "fourth in-step check must NOT yield once immune"
    # Immunity must not touch the counter further.
    assert await redis_async.get(key) == "3"


@pytest.mark.asyncio
async def test_no_yield_when_queue_empty(
    monkeypatch: pytest.MonkeyPatch, redis_async: aioredis.Redis
) -> None:
    """peek_top_due returns None → nothing to preempt by."""
    _patch_read_current_screen(monkeypatch)
    _patch_peek_top(monkeypatch, None)
    task = _make_task(effective_priority=80_000, redis_client=redis_async)
    assert await task._preempted_by_higher_priority("bs1", 1) is None


@pytest.mark.asyncio
async def test_no_yield_when_top_is_self(
    monkeypatch: pytest.MonkeyPatch, redis_async: aioredis.Redis
) -> None:
    """The top-of-queue can briefly be this same task (e.g. just re-enqueued
    after a step boundary). Yielding to ourselves would loop forever."""
    _patch_read_current_screen(monkeypatch)
    _patch_peek_top(
        monkeypatch,
        QueueItem(
            task_id="running-1",  # same task_id as running task
            player_id="p1",
            task_type="long_running_scenario",
            priority=99_000,
            run_at=0.0,
            instance_id="bs1",
            effective_priority=99_000,
        ),
    )
    task = _make_task(effective_priority=80_000, redis_client=redis_async)
    assert await task._preempted_by_higher_priority("bs1", 1) is None


@pytest.mark.asyncio
async def test_yield_falls_back_to_priority_when_effective_unset(
    monkeypatch: pytest.MonkeyPatch, redis_async: aioredis.Redis
) -> None:
    """Legacy tasks built without effective_priority must still use ``priority``
    as the comparator instead of treating 0 as "minimum"."""
    _patch_read_current_screen(monkeypatch)
    _patch_peek_top(
        monkeypatch,
        QueueItem(
            task_id="top-2",
            player_id="p1",
            task_type="x",
            priority=82_000,
            run_at=0.0,
            instance_id="bs1",
            effective_priority=0,  # legacy: not yet propagated
        ),
    )
    # priority=80_000, effective_priority=0 → comparator should use 80_000.
    # Top: effective_priority=0 → falls back to priority=82_000. Gap=2000 < margin → no yield.
    task = _make_task(effective_priority=0, redis_client=redis_async)
    assert await task._preempted_by_higher_priority("bs1", 1) is None


@pytest.mark.asyncio
async def test_redis_failure_is_safe_no_yield(
    monkeypatch: pytest.MonkeyPatch, redis_async: aioredis.Redis
) -> None:
    """peek_top_due raising must not crash the step loop — preempt = no-op."""
    async def boom(self, instance_id, *, current_screen=""):
        raise RuntimeError("redis down")

    monkeypatch.setattr(
        "scheduler.queue.RedisQueue.peek_top_due", boom, raising=True
    )
    _patch_read_current_screen(monkeypatch)

    task = _make_task(effective_priority=80_000, redis_client=redis_async)
    assert await task._preempted_by_higher_priority("bs1", 1) is None


def test_preempt_constants_match_adr() -> None:
    """ADR §5 defaults: PREEMPT_MARGIN=5_000, max yields = 3."""
    assert PREEMPT_MARGIN == 5_000
    assert PREEMPT_MAX_YIELDS == 3
