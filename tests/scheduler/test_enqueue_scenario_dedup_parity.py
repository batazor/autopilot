"""``_enqueue_scenario`` must enqueue through ``RedisQueue.schedule`` so DSL
``push_scenario`` and exec analyzers share the same atomic dedup semantics
and ``created_at`` tie-breaker as every other enqueue path.

Regression history: the helper used to do ``ZRANGEBYSCORE`` + ``ZADD``
non-atomically with a hand-rolled match-by-(player, task_type), which
- raced concurrent producers (the read + write was not in a single EVAL),
- skipped ``created_at`` (used for stable tie-breaking in ranking),
- treated a queued device-level item (``player_id=""``) as non-duplicate
  for a player-bound push, letting two equivalent pushes pile up.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tasks.dsl_scenario_helpers import _enqueue_scenario

if TYPE_CHECKING:
    import redis.asyncio as aioredis


async def _queue_payloads(redis: aioredis.Redis, instance_id: str) -> list[dict]:
    raw_items = await redis.zrangebyscore(f"wos:queue:{instance_id}", "-inf", "+inf")
    return [json.loads(r) for r in raw_items]


@pytest.mark.asyncio
async def test_enqueue_scenario_writes_created_at(redis_async: aioredis.Redis) -> None:
    """``RedisQueue.schedule`` stamps ``created_at`` for stable tie-breaks;
    the old hand-rolled enqueue dropped it."""
    ok = await _enqueue_scenario(
        redis_async=redis_async,
        instance_id="bs1",
        player_id="p1",
        scenario="claim_trials",
        priority=50_000,
        run_at=1_700_000_000.0,
        skip_if_duplicate=True,
    )
    assert ok is True

    items = await _queue_payloads(redis_async, "bs1")
    assert len(items) == 1
    assert "created_at" in items[0]
    assert isinstance(items[0]["created_at"], (int, float))
    assert items[0]["task_type"] == "claim_trials"
    assert items[0]["player_id"] == "p1"


@pytest.mark.asyncio
async def test_player_bound_push_treats_device_level_pending_as_duplicate(
    redis_async: aioredis.Redis,
) -> None:
    """Device-level pending item (``player_id=""``) must block a player-bound
    push of the same ``task_type`` on the same instance. The Lua dedup script
    handles this via ``device_level_enqueue or data_pid == "" or data_pid == X``;
    the old hand-rolled string-equals check missed the empty-string case and
    let the queue pile up two equivalent scenarios.
    """
    # Simulate a device-level item that the cron pusher (player_id="")
    # already landed in the queue.
    await redis_async.zadd(
        "wos:queue:bs1",
        {
            json.dumps(
                {
                    "task_id": "device-prior",
                    "player_id": "",
                    "task_type": "claim_trials",
                    "priority": 50_000,
                    "run_at": 1_700_000_000.0,
                    "instance_id": "bs1",
                }
            ): 1_700_000_000.0
        },
    )

    ok = await _enqueue_scenario(
        redis_async=redis_async,
        instance_id="bs1",
        player_id="p1",
        scenario="claim_trials",
        priority=50_000,
        run_at=1_700_000_005.0,
        skip_if_duplicate=True,
    )

    assert ok is False, "device-level pending must block player-bound push"
    items = await _queue_payloads(redis_async, "bs1")
    assert len(items) == 1
    assert items[0]["task_id"] == "device-prior"


@pytest.mark.asyncio
async def test_different_players_do_not_block_each_other(
    redis_async: aioredis.Redis,
) -> None:
    """Sanity: a queued push for player A must not block a push for player B
    when both are player-bound (the Lua check is ``device or data_pid=='' or
    data_pid==me``, so two non-empty distinct pids don't match)."""
    await _enqueue_scenario(
        redis_async=redis_async,
        instance_id="bs1",
        player_id="p1",
        scenario="claim_trials",
        priority=50_000,
        run_at=1_700_000_000.0,
        skip_if_duplicate=True,
    )
    ok = await _enqueue_scenario(
        redis_async=redis_async,
        instance_id="bs1",
        player_id="p2",
        scenario="claim_trials",
        priority=50_000,
        run_at=1_700_000_001.0,
        skip_if_duplicate=True,
    )

    assert ok is True
    items = await _queue_payloads(redis_async, "bs1")
    queued_pids = sorted(i["player_id"] for i in items)
    assert queued_pids == ["p1", "p2"], items


@pytest.mark.asyncio
async def test_skip_if_duplicate_false_still_enqueues_when_present(
    redis_async: aioredis.Redis,
) -> None:
    """When ``skip_if_duplicate=False`` we bypass the dedup gate entirely.
    Used for paths that explicitly want a stack of equivalent items."""
    await _enqueue_scenario(
        redis_async=redis_async,
        instance_id="bs1",
        player_id="p1",
        scenario="claim_trials",
        priority=50_000,
        run_at=1_700_000_000.0,
        skip_if_duplicate=True,
    )
    ok = await _enqueue_scenario(
        redis_async=redis_async,
        instance_id="bs1",
        player_id="p1",
        scenario="claim_trials",
        priority=50_000,
        run_at=1_700_000_001.0,
        skip_if_duplicate=False,
    )

    assert ok is True
    items = await _queue_payloads(redis_async, "bs1")
    assert len(items) == 2


@pytest.mark.asyncio
async def test_enqueue_scenario_validates_required_fields(
    redis_async: aioredis.Redis,
) -> None:
    """Empty inputs short-circuit before touching Redis — preserves prior
    contract that callers can pass through unsanitised state."""
    for kwargs in (
        {"scenario": "", "player_id": "p1", "instance_id": "bs1"},
        {"scenario": "x", "player_id": "", "instance_id": "bs1"},
        {"scenario": "x", "player_id": "p1", "instance_id": ""},
    ):
        ok = await _enqueue_scenario(
            redis_async=redis_async,
            priority=50_000,
            run_at=1_700_000_000.0,
            skip_if_duplicate=True,
            **kwargs,
        )
        assert ok is False, kwargs

    assert await redis_async.zcard("wos:queue:bs1") == 0
