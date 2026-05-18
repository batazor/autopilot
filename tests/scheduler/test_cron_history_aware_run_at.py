"""``_ensure_interval_cron_item`` honors recent_runs when computing ``run_at``.

Without this, a scheduler restart would re-fire a 4-hour cron the moment
its throttle key expires (which happens on restart, since the throttle is
TTL-bound in Redis but lost on Redis restart or on a key wipe). With
``last_run_at`` factored in, ``run_at = max(now, last_run + interval_s)``
so the next run lines up with the natural cadence.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from scenarios.cron_specs import scenario_loader_paths
from scenarios.evaluator import ScenarioEvaluator
from scenarios.loader import ScenarioLoader
from scheduler.optimizer import TaskOptimizer
from scheduler.queue import RedisQueue
from scheduler.runner import SchedulerRunner

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config.loader import Settings


def _make_runner(settings: Settings) -> SchedulerRunner:
    repo_root = Path(__file__).resolve().parents[2]
    return SchedulerRunner(
        settings,
        ScenarioLoader(scenario_loader_paths(repo_root)),
        TaskOptimizer(settings),
        ScenarioEvaluator(),
    )


@pytest.mark.asyncio
async def test_run_at_uses_last_run_plus_interval(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """Last run 1h ago, interval 4h → run_at ≈ now + 3h."""
    runner = _make_runner(settings)
    queue = RedisQueue(redis_async, settings)
    runner._redis = redis_async
    runner._queue = queue

    iid = "bs1"
    pid = "p1"
    task_type = "ping"
    interval_s = 4 * 60 * 60  # 4 hours
    now = time.time()

    # Seed: this task last ran 1 hour ago.
    await queue._append_recent_run(
        instance_id=iid, task_type=task_type, player_id=pid, now=now - 3600
    )

    await runner._ensure_interval_cron_item(
        name="ping_every_4h",
        spec_slug="ping_every_4h",
        expr="0 */4 * * *",
        task_type=task_type,
        priority=100,
        instance_id=iid,
        player_id=pid,
        interval_s=interval_s,
        now=now,
    )

    items = await queue.peek_all()
    assert len(items) == 1
    item = items[0]
    # Expected: last_run + interval ≈ (now - 3600) + 14400 = now + 10800.
    assert abs(item.run_at - (now + 10800)) < 30.0


@pytest.mark.asyncio
async def test_run_at_falls_back_to_now_on_cold_start(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """No history at all → enqueue at ``now`` (cold-start UX preserved)."""
    runner = _make_runner(settings)
    queue = RedisQueue(redis_async, settings)
    runner._redis = redis_async
    runner._queue = queue

    now = time.time()
    await runner._ensure_interval_cron_item(
        name="ping_every_4h",
        spec_slug="ping_every_4h",
        expr="0 */4 * * *",
        task_type="ping",
        priority=100,
        instance_id="bs_cold",
        player_id="p_cold",
        interval_s=4 * 60 * 60,
        now=now,
    )

    items = await queue.peek_all()
    assert len(items) == 1
    assert abs(items[0].run_at - now) < 5.0


@pytest.mark.asyncio
async def test_run_at_uses_now_when_overdue(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    """Last run was 5h ago for a 4h cron — we're already overdue, run now."""
    runner = _make_runner(settings)
    queue = RedisQueue(redis_async, settings)
    runner._redis = redis_async
    runner._queue = queue

    iid = "bs_overdue"
    pid = "p_overdue"
    now = time.time()
    await queue._append_recent_run(
        instance_id=iid, task_type="ping", player_id=pid, now=now - 5 * 3600
    )

    await runner._ensure_interval_cron_item(
        name="ping_every_4h",
        spec_slug="ping_every_4h",
        expr="0 */4 * * *",
        task_type="ping",
        priority=100,
        instance_id=iid,
        player_id=pid,
        interval_s=4 * 60 * 60,
        now=now,
    )

    items = await queue.peek_all()
    assert len(items) == 1
    assert abs(items[0].run_at - now) < 5.0
