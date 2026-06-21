"""``expires_at`` queue semantics: stale items are dropped at pop time.

``push_scenario.expires`` (e.g. daily-mission tasks that lose relevance at the
game-day reset) lands in the queue payload as an absolute ``expires_at``
deadline; ``pop_due`` must drop such items from the ZSET instead of running
them, while leaving unexpired and expiry-free items untouched.
"""

from __future__ import annotations

import time

import pytest

from config.loader import get_settings
from scheduler.queue import RedisQueue


def _queue_key(instance_id: str) -> str:
    return f"wos:queue:{instance_id}"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_drops_expired_item(redis_async: object) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    await q.schedule(
        task_id="t-expired",
        player_id="",
        task_type="who_i_am",
        priority=80_000,
        run_at=time.time() - 60.0,
        instance_id="bs1",
        skip_if_duplicate=False,
        expires_at=time.time() - 1.0,
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is None, "expired item must be dropped, not executed"
    assert await r.zcard(_queue_key("bs1")) == 0, "drop must remove it from the ZSET"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_runs_item_before_expiry(redis_async: object) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    await q.schedule(
        task_id="t-fresh",
        player_id="",
        task_type="who_i_am",
        priority=80_000,
        run_at=time.time(),
        instance_id="bs1",
        skip_if_duplicate=False,
        expires_at=time.time() + 3600.0,
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "who_i_am"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_pop_due_expired_item_does_not_shadow_next_candidate(
    redis_async: object,
) -> None:
    r = redis_async
    q = RedisQueue(r, get_settings())  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]

    await q.schedule(
        task_id="t-expired",
        player_id="",
        task_type="dismiss_unknown_popup",
        priority=90_000,
        run_at=time.time() - 120.0,
        instance_id="bs1",
        skip_if_duplicate=False,
        expires_at=time.time() - 1.0,
    )
    await q.schedule(
        task_id="t-live",
        player_id="",
        task_type="who_i_am",
        priority=80_000,
        run_at=time.time() - 60.0,
        instance_id="bs1",
        skip_if_duplicate=False,
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None
    assert item.task_type == "who_i_am"
    assert await r.zcard(_queue_key("bs1")) == 0, "expired sibling must be purged"
