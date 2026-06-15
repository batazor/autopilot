from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

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
