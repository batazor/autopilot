from __future__ import annotations

import asyncio
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
async def test_schedule_does_not_write_legacy_dup_index(redis_async: object) -> None:
    """``schedule()`` no longer writes the legacy ``wos:queue:idx:*`` SET.

    Locks in the removal of the duplicate index. Previously every schedule()
    issued a SADD that nothing read; the SET kept drifting from the ZSET truth
    until ``has_pending_duplicate`` was rewritten to scan the queue directly.
    """
    r = redis_async
    q = RedisQueue(r)  # type: ignore[arg-type]

    ok = await q.schedule(
        task_id="t1",
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

    # No SET key should be created under the dedup-index namespace.
    idx_keys = [k async for k in r.scan_iter(match="wos:queue:idx:*")]  # type: ignore[attr-defined]
    assert idx_keys == []


@pytest.mark.asyncio
async def test_has_pending_duplicate_scans_queue_zset_directly(
    redis_async: object,
) -> None:
    """``has_pending_duplicate`` reads the ZSET, not the (now-removed) idx.

    Seeds a payload directly via ZADD — exactly the shape that ``schedule()``
    writes — and verifies the dedup check finds it for both device-level and
    player-bound signatures.
    """
    queue = RedisQueue(redis_async)  # type: ignore[arg-type]

    payload = json.dumps(
        {
            "task_id": "ovl:bs1:claim_exploration_rewards:abc12345",
            "player_id": "p1",
            "task_type": "claim_exploration_rewards",
            "priority": 80000,
            "run_at": 1.0,
            "instance_id": "bs1",
            "region": "main_city.to.exploration",
        }
    )
    await redis_async.zadd("wos:queue:bs1", {payload: 1.0})  # type: ignore[attr-defined]

    # Device-level dedup (player_id="") finds the queued payload via ZSET scan.
    assert (
        await queue.has_pending_duplicate(
            player_id="",
            task_type="claim_exploration_rewards",
            region=None,
            instance_id="bs1",
            ignore_region=True,
        )
        is True
    )

    # Player-level enqueue is also blocked by the device-level item already in queue.
    assert (
        await queue.has_pending_duplicate(
            player_id="p1",
            task_type="claim_exploration_rewards",
            region="main_city.to.exploration",
            instance_id="bs1",
            ignore_region=True,
        )
        is True
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schedule_concurrent_dedup_atomic(redis_async: object) -> None:
    """Concurrent ``schedule(skip_if_duplicate=True)`` calls only enqueue once.

    Locks in the atomic ``_DEDUP_ZADD_LUA`` guard: the previous code did
    ``has_pending_duplicate()`` and ``zadd()`` in two separate round-trips,
    so two producers (rolling overlay tick + after-task overlay tick) could
    both pass the scan and both write the same logical scenario. With the
    Lua-serialized check, Redis itself is the synchronization point.
    """
    queue = RedisQueue(redis_async)  # type: ignore[arg-type]

    async def _enqueue(task_id: str) -> bool:
        return await queue.schedule(
            task_id=task_id,
            player_id="p1",
            task_type="claim_exploration_rewards",
            priority=80_000,
            run_at=1.0,
            instance_id="bs1",
            region="main_city.to.exploration",
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )

    results = await asyncio.gather(*[_enqueue(f"t{i}") for i in range(8)])
    assert results.count(True) == 1, f"exactly one winner expected, got {results}"
    assert results.count(False) == 7

    items = await redis_async.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    assert len(items) == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schedule_cross_player_same_task_type_does_not_dedup(
    redis_async: object,
) -> None:
    """Same ``task_type`` for two different players coexists in the queue.

    Player streams on a single instance are independent — an in-flight
    ``assign_worker`` for player A must not block enqueueing the same
    ``task_type`` for player B. The previous broad scan over-blocked here
    (Phase 1 matched any ``data.player_id``); the single-pass guard fixes it.
    """
    queue = RedisQueue(redis_async)  # type: ignore[arg-type]

    ok_a = await queue.schedule(
        task_id="a1",
        player_id="A",
        task_type="assign_worker",
        priority=80_000,
        run_at=1.0,
        instance_id="bs1",
        region="isWorkers",
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    ok_b = await queue.schedule(
        task_id="b1",
        player_id="B",
        task_type="assign_worker",
        priority=80_000,
        run_at=2.0,
        instance_id="bs1",
        region="isWorkers",
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    assert ok_a is True
    assert ok_b is True

    items = await redis_async.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    assert len(items) == 2
    pids = sorted(json.loads(it)["player_id"] for it in items)
    assert pids == ["A", "B"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_schedule_device_level_blocks_subsequent_player_enqueue(
    redis_async: object,
) -> None:
    """A device-level (``player_id=""``) item in flight blocks player-bound enqueues.

    Device-level scenarios (e.g. ``who_i_am`` identity probe) are mutually
    exclusive with player-bound work on the same ``(instance, task_type)`` —
    that's the only case where the dedup intentionally crosses player streams.
    """
    queue = RedisQueue(redis_async)  # type: ignore[arg-type]

    ok_device = await queue.schedule(
        task_id="dev1",
        player_id="",
        task_type="who_i_am",
        priority=90_000,
        run_at=1.0,
        instance_id="bs1",
        region=None,
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    ok_player = await queue.schedule(
        task_id="p1",
        player_id="A",
        task_type="who_i_am",
        priority=90_000,
        run_at=2.0,
        instance_id="bs1",
        region=None,
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    assert ok_device is True
    assert ok_player is False

    items = await redis_async.zrange("wos:queue:bs1", 0, -1)  # type: ignore[attr-defined]
    assert len(items) == 1
    assert json.loads(items[0])["player_id"] == ""
