"""``InstanceWorkerTasksMixin._apply_planner_post_consume`` bumps the daily
quota counter the planner reads back, for BOTH the stamina and resource
planners, and applies the stamina estimate delta.

The resource case is the bug guarded here: the worker historically bumped only
``stamina_quota_id``/``stamina_period``, never the ``resource_action_id``/
``resource_period`` markers the resource planner emits — yet the resource
allocator reads usage back via the SAME ``quota_field(period, id)``. Without the
bump a capped ``daily_quota`` action's ``quota_used`` stays 0 and it re-dispatches
without bound once the resource planner runs as a dispatcher.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from games.wos.core.stamina.model import quota_field

from scheduler.queue import QueueItem
from worker.instance_worker_tasks import InstanceWorkerTasksMixin

if TYPE_CHECKING:
    import redis.asyncio as aioredis


def _make_mixin(redis: aioredis.Redis) -> InstanceWorkerTasksMixin:
    obj = object.__new__(InstanceWorkerTasksMixin)
    obj._cfg = SimpleNamespace(instance_id="bs1")  # type: ignore[attr-defined]
    obj._redis = redis  # type: ignore[attr-defined]
    obj._queue = None  # type: ignore[attr-defined]
    return obj


def _qitem(args: dict[str, Any] | None) -> QueueItem:
    return QueueItem(
        task_id="t1",
        player_id="p1",
        task_type="dsl_scenario",
        priority=0,
        run_at=0.0,
        instance_id="bs1",
        args=args,
    )


@pytest.mark.asyncio
async def test_resource_consumer_bumps_resource_quota_counter(
    redis_async: aioredis.Redis,
) -> None:
    mixin = _make_mixin(redis_async)
    await mixin._apply_planner_post_consume(  # type: ignore[attr-defined]
        _qitem({"resource_action_id": "bear", "resource_period": "20260626"})
    )
    field = quota_field("20260626", "bear")
    assert await redis_async.hget("wos:player:p1:state", field) == "1"


@pytest.mark.asyncio
async def test_stamina_consumer_bumps_quota_and_applies_delta(
    redis_async: aioredis.Redis,
) -> None:
    mixin = _make_mixin(redis_async)
    await redis_async.hset("wos:player:p1:state", "stamina", "200")
    await mixin._apply_planner_post_consume(  # type: ignore[attr-defined]
        _qitem(
            {
                "stamina_quota_id": "intel",
                "stamina_period": "20260626",
                "stamina_delta": -10,
            }
        )
    )
    assert (
        await redis_async.hget("wos:player:p1:state", quota_field("20260626", "intel"))
        == "1"
    )
    assert await redis_async.hget("wos:player:p1:state", "stamina") == "190"


@pytest.mark.asyncio
async def test_both_planners_in_one_task_bump_independently(
    redis_async: aioredis.Redis,
) -> None:
    """A task may carry both marker pairs — each is bumped under its own field."""
    mixin = _make_mixin(redis_async)
    await mixin._apply_planner_post_consume(  # type: ignore[attr-defined]
        _qitem(
            {
                "stamina_quota_id": "intel",
                "stamina_period": "20260626",
                "resource_action_id": "bear",
                "resource_period": "20260626",
            }
        )
    )
    assert (
        await redis_async.hget("wos:player:p1:state", quota_field("20260626", "intel"))
        == "1"
    )
    assert (
        await redis_async.hget("wos:player:p1:state", quota_field("20260626", "bear"))
        == "1"
    )


@pytest.mark.asyncio
async def test_no_markers_leaves_state_untouched(redis_async: aioredis.Redis) -> None:
    mixin = _make_mixin(redis_async)
    await mixin._apply_planner_post_consume(  # type: ignore[attr-defined]
        _qitem({"unrelated": "x"})
    )
    assert await redis_async.hgetall("wos:player:p1:state") == {}


@pytest.mark.asyncio
async def test_stamina_delta_floors_at_zero(redis_async: aioredis.Redis) -> None:
    mixin = _make_mixin(redis_async)
    await redis_async.hset("wos:player:p1:state", "stamina", "5")
    await mixin._apply_planner_post_consume(  # type: ignore[attr-defined]
        _qitem({"stamina_delta": -20})
    )
    assert await redis_async.hget("wos:player:p1:state", "stamina") == "0"
