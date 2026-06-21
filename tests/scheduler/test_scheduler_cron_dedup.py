from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

import scheduler.runner as runner_module
from scheduler.queue import RedisQueue
from scheduler.runner import SchedulerRunner

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config.loader import Settings


def _make_runner(settings: Settings) -> SchedulerRunner:
    return SchedulerRunner(settings)


@pytest.mark.asyncio
async def test_interval_cron_dedups_pending_duplicate_across_ticks(
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    runner = _make_runner(settings)
    queue = RedisQueue(redis_async, settings)
    runner._redis = redis_async
    runner._queue = queue
    now = time.time()

    for _ in range(3):
        await runner._ensure_interval_cron_item(
            name="check_city",
            spec_slug="check_city",
            expr="*/5 * * * *",
            task_type="check_main_city",
            priority=10,
            instance_id="bs1",
            player_id="p1",
            interval_s=300,
            now=now,
        )

    items = await queue.peek_all()
    assert len(items) == 1, [(i.task_id, i.task_type) for i in items]
    assert items[0].task_type == "check_main_city"


@pytest.mark.asyncio
async def test_interval_cron_skips_enqueue_when_task_already_running(
    redis_async: aioredis.Redis,
    settings: Settings,
) -> None:
    await redis_async.set(
        "wos:queue:running:bs1",
        json.dumps(
            {
                "task_id": "in-flight",
                "task_type": "claim_trials",
                "player_id": "p1",
                "instance_id": "bs1",
            }
        ),
        ex=180,
    )

    runner = _make_runner(settings)
    queue = RedisQueue(redis_async, settings)
    runner._redis = redis_async
    runner._queue = queue
    now = time.time()

    await runner._ensure_interval_cron_item(
        name="claim_trials",
        spec_slug="claim_trials",
        expr="*/5 * * * *",
        task_type="claim_trials",
        priority=10,
        instance_id="bs1",
        player_id="p1",
        interval_s=300,
        now=now,
    )
    await runner._ensure_interval_cron_item(
        name="check_city",
        spec_slug="check_city",
        expr="*/5 * * * *",
        task_type="check_main_city",
        priority=10,
        instance_id="bs1",
        player_id="p1",
        interval_s=300,
        now=now,
    )

    items = await queue.peek_all()
    queued_types = sorted(i.task_type for i in items)
    assert queued_types == ["check_main_city"], [(i.task_id, i.task_type) for i in items]


@pytest.mark.asyncio
async def test_cron_min_furnace_level_gates_check_main_city(
    redis_async: aioredis.Redis,
    settings: Settings,
    mocker,
) -> None:
    """``check_main_city`` (min_furnace_level: 5) stays out of the queue during
    onboarding (furnace < 5) and is published once past it — gated at the
    publish point, not via a scenario cond."""
    runner = _make_runner(settings)
    runner._redis = redis_async
    runner._queue = RedisQueue(redis_async, settings)

    # Give the publish loop a player so interval crons reach _ensure_interval_cron_item.
    mocker.patch.object(
        runner_module, "player_ids_for_device_candidates", new=lambda *_a, **_k: ["p1"]
    )
    published: list[str] = []

    async def _ensure(*, task_type: str, **_kw) -> None:
        published.append(task_type)

    mocker.patch.object(runner, "_ensure_interval_cron_item", new=_ensure)

    async def _furnace(level: int):
        async def _read(_instance_id: str) -> int:
            return level

        return _read

    # Onboarding: furnace 4 → check_main_city not published (others still are).
    mocker.patch.object(runner, "_instance_furnace_level", new=await _furnace(4))
    await runner._run_cron_specs()
    assert published, "expected the publish loop to consider some crons"
    assert "check_main_city" not in published

    # Past onboarding: furnace 12 → check_main_city published.
    published.clear()
    mocker.patch.object(runner, "_instance_furnace_level", new=await _furnace(12))
    await runner._run_cron_specs()
    assert "check_main_city" in published


@pytest.mark.asyncio
async def test_cron_min_furnace_level_bypassed_by_resolved_active_player(
    redis_async: aioredis.Redis,
    settings: Settings,
    mocker,
) -> None:
    """A resolved active_player means who_i_am has run (past onboarding — it's
    gated until furnace >= 5), so min_furnace_level crons must publish even when
    the furnace reader hasn't populated the level (reads 0). Otherwise a
    developed account stays gated forever and never gets its return-home cron."""
    runner = _make_runner(settings)
    runner._redis = redis_async
    runner._queue = RedisQueue(redis_async, settings)

    mocker.patch.object(
        runner_module, "player_ids_for_device_candidates", new=lambda *_a, **_k: ["p1"]
    )
    published: list[str] = []

    async def _ensure(*, task_type: str, **_kw) -> None:
        published.append(task_type)

    mocker.patch.object(runner, "_ensure_interval_cron_item", new=_ensure)

    async def _read_zero(_instance_id: str) -> int:  # furnace reader unbuilt → 0
        return 0

    mocker.patch.object(runner, "_instance_furnace_level", new=_read_zero)

    # Furnace reads 0 and no active_player yet → check_main_city stays gated.
    await runner._run_cron_specs()
    assert published, "expected the publish loop to consider some crons"
    assert "check_main_city" not in published

    # Resolve active_player on every instance → gate is bypassed despite furnace 0.
    for inst in settings.instances:
        await redis_async.hset(
            f"wos:instance:{inst.instance_id}:state", "active_player", "p1"
        )
    published.clear()
    await runner._run_cron_specs()
    assert "check_main_city" in published
