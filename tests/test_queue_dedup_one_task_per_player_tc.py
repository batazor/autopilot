from __future__ import annotations

import json

import pytest

from scheduler.queue import RedisQueue


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schedule_dedup_ignore_region_enforces_one_task_per_player(
    redis_async: object,
) -> None:
    r = redis_async
    q = RedisQueue(r)  # type: ignore[arg-type]

    ok1 = await q.schedule(
        task_id="t1",
        player_id="765502864",
        task_type="assign_worker",
        priority=80_000,
        run_at=1.0,
        instance_id="bs1",
        region="isWorkers",
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    ok2 = await q.schedule(
        task_id="t2",
        player_id="765502864",
        task_type="assign_worker",
        priority=80_000,
        run_at=2.0,
        instance_id="bs1",
        region="page.worker.add",
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )

    assert ok1 is True
    assert ok2 is False

    items = await r.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    assert len(items) == 1
    doc = json.loads(items[0])
    assert doc["task_type"] == "assign_worker"
    assert doc["player_id"] == "765502864"
    # Source stays as metadata (not part of dedup).
    assert doc["region"] == "isWorkers"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schedule_self_heals_stale_dup_index(redis_async: object) -> None:
    """Regression: dup-index may outlive queue items and must not block scheduling."""
    r = redis_async
    q = RedisQueue(r)  # type: ignore[arg-type]

    # Simulate a stale idx entry for ignore-region signature (region is empty in key).
    stale_key = "wos:queue:idx:bs1:chapter_task_router::"
    await r.sadd(stale_key, '{"task_id":"stale"}')  # type: ignore[attr-defined]
    items = await r.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    assert items == []

    ok = await q.schedule(
        task_id="t-new",
        player_id="765502864",
        task_type="chapter_task_router",
        priority=70_000,
        run_at=1.0,
        instance_id="bs1",
        region="chapter.task",
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    assert ok is True


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_cleans_ignore_region_dup_index(redis_async: object) -> None:
    """Regression: when dedup_ignore_region is used, pop_due must remove idx with region=''."""
    r = redis_async
    q = RedisQueue(r)  # type: ignore[arg-type]

    # Make task runnable (active_player set so pop_due doesn't gate it).
    await r.hset("wos:instance:bs1:state", mapping={"active_player": "765502864"})  # type: ignore[attr-defined]

    ok = await q.schedule(
        task_id="t1",
        player_id="765502864",
        task_type="chapter_task_router",
        priority=70_000,
        run_at=0.0,
        instance_id="bs1",
        region="chapter.task",
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    assert ok is True

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "chapter_task_router"

    # idx key for ignore-region must be empty now
    idx_key = "wos:queue:idx:bs1:chapter_task_router::765502864"
    # Note: for device-level pushes player_id may be '', but here it's specific.
    assert int(await r.scard(idx_key)) == 0  # type: ignore[attr-defined]

