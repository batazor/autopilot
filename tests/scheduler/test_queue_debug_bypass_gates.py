"""Verify ``debug: true`` payloads bypass active_player + node gates in ``pop_due``.

The debug UI ("Run scenario now" on ``ui/views/debug_scenarios.py``) writes
queue items with ``debug: True`` directly via ``zadd``. These items must run
even when ``active_player`` and ``current_screen`` are still empty —
otherwise the user clicks the button and nothing happens.
"""

from __future__ import annotations

import json
import time

import pytest

from config.loader import get_settings
from scheduler.queue import RedisQueue


def _zadd_debug_payload(
    redis_async,
    *,
    instance_id: str,
    task_type: str,
    player_id: str = "",
    priority: int = 80_000,
    debug: bool = True,
):
    now = time.time()
    payload = {
        "task_id": f"ui:debug:{instance_id}:{task_type}:{int(now)}",
        "player_id": player_id,
        "task_type": task_type,
        "priority": priority,
        "run_at": now,
        "instance_id": instance_id,
        "debug": debug,
        "source": "ui.debug_scenarios",
    }
    return redis_async.zadd(
        f"wos:queue:{instance_id}",
        {json.dumps(payload, ensure_ascii=False): now},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_debug_payload_pops_without_active_player(redis_async) -> None:
    """A non-device-level scenario with ``debug: true`` runs even with no active_player."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await _zadd_debug_payload(redis_async, instance_id="bs1", task_type="assign_worker")

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is not None, "debug-flagged task must bypass the active_player gate"
    assert item.task_type == "assign_worker"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_debug_payload_pops_with_empty_current_screen(redis_async) -> None:
    """A node-requiring scenario with ``debug: true`` runs even with no current_screen."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await redis_async.hset(
        "wos:instance:bs1:state", mapping={"active_player": "765502864"}
    )
    # ``squad_fight`` is the user's case from the conversation — push_scenario in YAML.
    await _zadd_debug_payload(
        redis_async, instance_id="bs1", task_type="squad_fight", player_id="765502864"
    )

    item = await q.pop_due("bs1", current_screen="")
    assert item is not None, "debug-flagged task must bypass the current_screen gate"
    assert item.task_type == "squad_fight"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_non_debug_payload_still_gated(redis_async) -> None:
    """Regression guard: removing ``debug`` flag restores normal gating."""
    q = RedisQueue(redis_async, get_settings())  # type: ignore[arg-type]
    await _zadd_debug_payload(
        redis_async, instance_id="bs1", task_type="assign_worker", debug=False
    )

    item = await q.pop_due("bs1", current_screen="main_city")
    assert item is None, "without debug flag, player-bound task must still be gated"
