"""Focus mode: ``pop_due`` runs ONLY the pinned scenario, parks the rest.

When ``wos:instance:<id>:state.focus_scenario`` is set, the queue must surface
only matching items so leftover cron work / autonomously-pushed scenarios stay
in the queue and never execute. This is what makes the fish-detect Play button
(and ``botctl run --focus``) actually run a single scenario instead of the full
autopilot. See ``RedisQueue._collect_ranked_due``.
"""

from __future__ import annotations

import json
import time

import pytest

from config.loader import get_settings
from scheduler.queue import RedisQueue


def _zadd(redis_async, *, instance_id: str, task_type: str, player_id: str = ""):
    now = time.time()
    payload = {
        "task_id": f"q:{instance_id}:{task_type}:{int(now * 1000)}",
        "player_id": player_id,
        "task_type": task_type,
        "priority": 80_000,
        "run_at": now,
        "instance_id": instance_id,
    }
    return redis_async.zadd(
        f"wos:queue:{instance_id}",
        {json.dumps(payload, ensure_ascii=False): now},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_focus_pops_only_matching_scenario(redis_async) -> None:
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await redis_async.hset(
        "wos:instance:bs1:state",
        mapping={
            "active_player": "401227964",
            "focus_scenario": "event.fishing_tournament",
        },
    )
    # Two due device-level scenarios; only the focused one may pop.
    await _zadd(redis_async, instance_id="bs1", task_type="check_main_city")
    await _zadd(redis_async, instance_id="bs1", task_type="event.fishing_tournament")

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "event.fishing_tournament"

    # The non-focus item is parked, not executed: nothing else pops.
    assert await q.pop_due("bs1", current_screen="main_city") is None
    # ...and it is still in the queue (dropped from results, not deleted).
    remaining = await redis_async.zcard("wos:queue:bs1")
    assert remaining == 1


@pytest.mark.integration
@pytest.mark.asyncio
async def test_focus_blocks_when_only_non_focus_present(redis_async) -> None:
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await redis_async.hset(
        "wos:instance:bs1:state",
        mapping={
            "active_player": "401227964",
            "focus_scenario": "event.fishing_tournament",
        },
    )
    await _zadd(redis_async, instance_id="bs1", task_type="check_main_city")

    assert await q.pop_due("bs1", current_screen="main_city") is None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_no_focus_pops_normally(redis_async) -> None:
    """Regression guard: without focus set, normal gating/ranking is unchanged."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await redis_async.hset(
        "wos:instance:bs1:state", mapping={"active_player": "401227964"}
    )
    await _zadd(redis_async, instance_id="bs1", task_type="check_main_city")

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "check_main_city"
