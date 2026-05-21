"""Pending queue UI order matches ``pop_due`` ranking per instance."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

from dashboard.redis_client import fetch_pending_execution_order

if TYPE_CHECKING:
    from testcontainers.redis import RedisContainer


@pytest.mark.integration
def test_pending_execution_order_ranks_higher_priority_first(
    redis_sync: object, redis_container: RedisContainer
) -> None:
    now = time.time()
    low = {
        "task_id": "t-low",
        "player_id": "",
        "task_type": "who_i_am",
        "priority": 10_000,
        "run_at": now - 2,
        "instance_id": "bs1",
        "created_at": now - 2,
    }
    high = {
        "task_id": "t-high",
        "player_id": "",
        "task_type": "who_i_am",
        "priority": 90_000,
        "run_at": now - 1,
        "instance_id": "bs1",
        "created_at": now - 1,
    }
    redis_sync.zadd(  # type: ignore[attr-defined]
        "wos:queue:bs1",
        {
            json.dumps(low): float(low["run_at"]),
            json.dumps(high): float(high["run_at"]),
        },
    )

    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    url = f"redis://{host}:{port}/0"

    order = fetch_pending_execution_order(
        redis_sync,  # type: ignore[arg-type]
        "bs1",
        current_screen="main_city",
        redis_url=url,
    )
    assert order == ["t-high", "t-low"]


@pytest.mark.integration
def test_pending_execution_order_future_tasks_after_due(
    redis_sync: object, redis_container: RedisContainer
) -> None:
    now = time.time()
    future = {
        "task_id": "t-future",
        "player_id": "",
        "task_type": "who_i_am",
        "priority": 99_000,
        "run_at": now + 3600,
        "instance_id": "bs1",
        "created_at": now,
    }
    due = {
        "task_id": "t-due",
        "player_id": "",
        "task_type": "who_i_am",
        "priority": 50_000,
        "run_at": now - 1,
        "instance_id": "bs1",
        "created_at": now - 1,
    }
    redis_sync.zadd(  # type: ignore[attr-defined]
        "wos:queue:bs1",
        {
            json.dumps(future): float(future["run_at"]),
            json.dumps(due): float(due["run_at"]),
        },
    )

    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    url = f"redis://{host}:{port}/0"

    order = fetch_pending_execution_order(
        redis_sync,  # type: ignore[arg-type]
        "bs1",
        current_screen="main_city",
        redis_url=url,
    )
    assert order == ["t-due", "t-future"]
