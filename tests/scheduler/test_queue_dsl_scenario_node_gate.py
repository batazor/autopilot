"""Notify/optimizer pushes (``task_type='dsl_scenario'``) must respect the
screen-identity park gate the same way cron pushes do.

Cron enqueues a ``node:`` scenario with ``task_type`` set to the scenario key,
so the pop-time gate (``_task_types_requiring_node``) parks it while
``current_screen`` is empty. Notify (``notify.publisher.enqueue_scenario``) and
the optimizer dispatcher instead enqueue the generic ``task_type='dsl_scenario'``
with the real key in the ``dsl_scenario`` field. Before the
``_effective_task_type`` resolution those pushes slipped past the gate, got
popped on an unknown screen, and burned on the DSL ``awaiting_screen_identity``
early-exit — re-queued every 5s in a hot loop that polluted run history.
"""

from __future__ import annotations

import json
import time

import pytest

from config.loader import get_settings
from scheduler.queue import RedisQueue


def _zadd_notify_scenario(
    redis_async,
    *,
    instance_id: str,
    dsl_scenario: str,
    player_id: str,
    priority: int = 80_000,
):
    """Mirror ``notify.publisher.enqueue_scenario``: generic task_type, key in
    the ``dsl_scenario`` field, no ``debug`` flag."""
    now = time.time()
    payload = {
        "task_id": f"notify:{instance_id}:{dsl_scenario}:{int(now)}",
        "player_id": player_id,
        "task_type": "dsl_scenario",
        "dsl_scenario": dsl_scenario,
        "priority": priority,
        "run_at": now,
        "instance_id": instance_id,
        "created_at": now,
    }
    return redis_async.zadd(
        f"wos:queue:{instance_id}",
        {json.dumps(payload, ensure_ascii=False): now},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_notify_node_scenario_parked_when_current_screen_empty(redis_async) -> None:
    """``intel_lighthouse`` (``node: intel``) pushed by notify must be parked —
    not popped — while ``current_screen`` is empty, so it can't burn on
    ``awaiting_screen_identity``."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await redis_async.hset(
        "wos:instance:bs1:state", mapping={"active_player": "401227964"}
    )
    await _zadd_notify_scenario(
        redis_async,
        instance_id="bs1",
        dsl_scenario="intel_lighthouse",
        player_id="401227964",
    )

    item = await q.pop_due("bs1", current_screen="")
    assert item is None, (
        "notify-pushed node scenario must be parked while current_screen is empty"
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_notify_node_scenario_pops_once_screen_known(redis_async) -> None:
    """Same push pops normally once ``current_screen`` is a known node — the
    gate only parks, it never drops the task."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await redis_async.hset(
        "wos:instance:bs1:state", mapping={"active_player": "401227964"}
    )
    await _zadd_notify_scenario(
        redis_async,
        instance_id="bs1",
        dsl_scenario="intel_lighthouse",
        player_id="401227964",
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None, "task must pop once current_screen is known"
    assert item.task_type == "dsl_scenario"
    assert item.dsl_scenario == "intel_lighthouse"
