"""``has_pending_duplicate`` and ``remove_by_task_type`` identify queue items by
their *logical* task type, so a generic DSL envelope (``task_type="dsl_scenario"``
carrying the scenario key in ``dsl_scenario`` — what notify and the optimizer
dispatcher push) is matched by its scenario key, consistent with the atomic Lua
dedup. Without this a cron whose key equals that scenario would not see the
pending generic item and would enqueue a duplicate.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

from scheduler.queue import RedisQueue

if TYPE_CHECKING:
    import redis.asyncio as aioredis

    from config.loader import Settings


async def _enqueue_generic(
    queue: RedisQueue, *, instance_id: str, player_id: str, scenario: str, task_id: str
) -> None:
    """Enqueue a notify/optimizer-style envelope (task_type='dsl_scenario')."""
    await queue.schedule(
        task_id=task_id,
        player_id=player_id,
        task_type="dsl_scenario",
        priority=50_000,
        run_at=time.time(),
        instance_id=instance_id,
        dsl_scenario=scenario,
    )


@pytest.mark.asyncio
async def test_has_pending_duplicate_matches_generic_payload_by_scenario(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    queue = RedisQueue(redis_async, settings)
    await _enqueue_generic(
        queue, instance_id="bs1", player_id="p1", scenario="claim_mail", task_id="g1"
    )

    # A cron asking by the scenario key sees the generic envelope as a duplicate.
    assert (
        await queue.has_pending_duplicate(
            player_id="p1",
            task_type="claim_mail",
            region=None,
            instance_id="bs1",
            ignore_region=True,
        )
        is True
    )
    # A different scenario must NOT match — they only share the transport type.
    assert (
        await queue.has_pending_duplicate(
            player_id="p1",
            task_type="claim_trials",
            region=None,
            instance_id="bs1",
            ignore_region=True,
        )
        is False
    )
    # And the literal transport string is not a scenario, so it must not match.
    assert (
        await queue.has_pending_duplicate(
            player_id="p1",
            task_type="dsl_scenario",
            region=None,
            instance_id="bs1",
            ignore_region=True,
        )
        is False
    )


@pytest.mark.asyncio
async def test_remove_by_task_type_evicts_generic_payload_by_scenario(
    redis_async: aioredis.Redis, settings: Settings
) -> None:
    queue = RedisQueue(redis_async, settings)
    await _enqueue_generic(
        queue, instance_id="bs1", player_id="p1", scenario="claim_mail", task_id="g1"
    )
    await _enqueue_generic(
        queue, instance_id="bs1", player_id="p1", scenario="claim_trials", task_id="g2"
    )

    removed = await queue.remove_by_task_type("claim_mail", "bs1")
    assert removed == 1

    survivors = [i.dsl_scenario for i in await queue.peek_all()]
    assert survivors == ["claim_trials"]
