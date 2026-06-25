"""``SchedulerRunner._run_resource_planner`` must not leak a phantom reservation.

The reservation is created BEFORE enqueue (its id rides in the task args so the
worker can confirm/release it). But ``enqueue_decision`` uses
``skip_if_duplicate`` and can drop the push when an identical action is already
pending/in-flight — and that one already holds its own reservation. The runner
must roll our reservation back on a dropped enqueue, else it sits as a phantom
hold (subtracting slots/troops/heroes from the world view) until its
``confirm_by`` TTL.

``plan`` + ``enqueue_decision`` are stubbed (the planner is dormant); ``reserve``
and ``release`` stay REAL so the assertion exercises the actual ledger.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from games.wos.core.resources import adapter as resource_adapter
from games.wos.core.resources import allocator as alloc

from scheduler.queue import RedisQueue
from scheduler.runner import SchedulerRunner

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config.loader import Settings


def _consume_decision() -> alloc.Decision:
    return alloc.Decision(
        action=alloc.CONSUME,
        reason="consume",
        task_type="bear.rally",
        target_id="bear",
        priority=90,
        stamina_delta=-10,
        slot_cost=1,
        lease_seconds=3600,
    )


def _runner_with_stubs(
    redis_async: aioredis.Redis,
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    *,
    enqueue_returns: bool,
) -> SchedulerRunner:
    runner = SchedulerRunner(settings)
    runner._redis = redis_async
    runner._queue = RedisQueue(redis_async, settings)

    dec = _consume_decision()

    async def _read_ledger(*_a: object, **_k: object) -> list:
        return []

    async def _enqueue(*_a: object, **_k: object) -> bool:
        return enqueue_returns

    async def _noop(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr(resource_adapter, "load_table", lambda *_a, **_k: SimpleNamespace(enabled=True))
    monkeypatch.setattr(resource_adapter, "read_ledger", _read_ledger)
    monkeypatch.setattr(
        resource_adapter, "plan", lambda *_a, **_k: SimpleNamespace(decision=dec, period="20260626")
    )
    monkeypatch.setattr(resource_adapter, "write_decision_trace", _noop)
    monkeypatch.setattr(resource_adapter, "enqueue_decision", _enqueue)
    # reserve + release stay REAL.
    return runner


@pytest.mark.asyncio
async def test_releases_reservation_when_enqueue_deduped(
    redis_async: aioredis.Redis, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _runner_with_stubs(redis_async, settings, monkeypatch, enqueue_returns=False)

    await runner._run_resource_planner({"p1": {}}, {"p1": "bs1"}, time.time())

    # reserve wrote an entry, release removed it → no phantom hold left behind.
    assert await redis_async.hgetall("wos:player:p1:resource_reservations") == {}


@pytest.mark.asyncio
async def test_keeps_reservation_when_enqueue_succeeds(
    redis_async: aioredis.Redis, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Positive control: a real enqueue keeps the reservation held (no release)."""
    runner = _runner_with_stubs(redis_async, settings, monkeypatch, enqueue_returns=True)

    await runner._run_resource_planner({"p1": {}}, {"p1": "bs1"}, time.time())

    held = await redis_async.hgetall("wos:player:p1:resource_reservations")
    assert len(held) == 1
    assert '"action_id": "bear"' in next(iter(held.values()))
