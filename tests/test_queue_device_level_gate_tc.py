"""Integration tests for the ``device_level`` gate in ``RedisQueue.pop_due``.

These mirror the old mock-based tests, but exercise real Redis semantics
(sorted set ordering, JSON payloads, and duplicate index maintenance).
"""

from __future__ import annotations

import time

import pytest

from config.loader import get_settings
from scheduler.queue import RedisQueue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_blocks_player_bound_scenario_when_active_player_missing(
    redis_async: object,
) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]

    # active_player is missing/empty by default
    await q.schedule(
        task_id="t-assign",
        player_id="",
        task_type="assign_worker",
        priority=80_000,
        run_at=time.time(),
        instance_id="bs1",
        skip_if_duplicate=False,
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is None, "assign_worker is player-bound and must be gated"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_allows_device_level_scenario_when_active_player_missing(
    redis_async: object,
) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]

    await q.schedule(
        task_id="t-who",
        player_id="",
        task_type="who_i_am",
        priority=82_000,
        run_at=time.time(),
        instance_id="bs1",
        skip_if_duplicate=False,
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "who_i_am"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_prefers_device_level_when_player_bound_outranks(
    redis_async: object,
) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]

    # Higher-priority routine task must NOT preempt seed when player is unknown.
    now = time.time()
    await q.schedule(
        task_id="t-assign",
        player_id="",
        task_type="assign_worker",
        priority=80_000,
        run_at=now,
        instance_id="bs1",
        skip_if_duplicate=False,
    )
    await q.schedule(
        task_id="t-who",
        player_id="",
        task_type="who_i_am",
        priority=82_000,
        # Must be runnable at the same time slice as the competing item.
        run_at=now,
        instance_id="bs1",
        skip_if_duplicate=False,
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "who_i_am"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_releases_player_bound_scenario_once_active_player_set(
    redis_async: object,
) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]

    # Mimic who_i_am writing active_player on instance state.
    await r.hset("wos:instance:bs1:state", mapping={"active_player": "765502864"})  # type: ignore[attr-defined]

    await q.schedule(
        task_id="t-assign",
        player_id="",
        task_type="assign_worker",
        priority=80_000,
        run_at=time.time(),
        instance_id="bs1",
        skip_if_duplicate=False,
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "assign_worker"

