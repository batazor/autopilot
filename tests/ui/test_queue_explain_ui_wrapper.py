"""``ui.redis_client.fetch_queue_explain_rows`` powers the Queue page's
"Why this order?" panel — it's a sync ``asyncio.run`` shim around
:meth:`RedisQueue.explain_top_n`. This test pins the shape of the dict it
returns so the UI render code keeps lining up with the underlying ranking.
"""

from __future__ import annotations

import json
import time

import pytest
from testcontainers.redis import RedisContainer

from ui.redis_client import fetch_queue_explain_rows


@pytest.mark.integration
def test_fetch_queue_explain_rows_returns_breakdown(
    redis_sync: object, redis_container: RedisContainer
) -> None:
    """End-to-end: enqueue a due item, call the sync wrapper, assert every
    key the UI renders is present and typed correctly."""
    body = {
        "task_id": "t-who",
        "player_id": "",  # device_level, bypasses active_player gate
        "task_type": "who_i_am",
        "priority": 82_000,
        "run_at": time.time() - 1,
        "instance_id": "bs1",
        "created_at": time.time() - 1,
    }
    redis_sync.zadd(  # type: ignore[attr-defined]
        "wos:queue:bs1", {json.dumps(body): float(body["run_at"])}
    )

    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    url = f"redis://{host}:{port}/0"

    rows = fetch_queue_explain_rows(
        instance_id="bs1",
        current_screen="main_city",
        n=10,
        redis_url=url,
    )
    assert len(rows) == 1
    r = rows[0]

    # Identity fields the UI table shows in left columns.
    assert r["task_type"] == "who_i_am"
    assert r["player_id"] == ""

    # Ranking breakdown the "Why this order?" panel surfaces.
    assert r["base_priority"] == 82_000
    assert isinstance(r["effective_priority"], int)
    assert r["effective_priority"] <= r["base_priority"]
    for key in ("graph_debuff", "recent_debuff", "hops", "recent_count"):
        assert key in r, f"missing breakdown field: {key}"
        assert isinstance(r[key], int)
    assert isinstance(r["reachable"], bool)
    assert "required_node" in r


@pytest.mark.integration
def test_fetch_queue_explain_rows_empty_queue_returns_empty_list(
    redis_container: RedisContainer,
) -> None:
    """No due candidates → empty list (UI shows the "no candidates" caption)."""
    host = redis_container.get_container_host_ip()
    port = int(redis_container.get_exposed_port(6379))
    url = f"redis://{host}:{port}/0"

    rows = fetch_queue_explain_rows(
        instance_id="bs-nonexistent",
        current_screen="main_city",
        n=10,
        redis_url=url,
    )
    assert rows == []


def test_fetch_queue_explain_rows_swallows_connection_errors() -> None:
    """Unreachable Redis returns ``[]`` so the fragment doesn't crash the page."""
    rows = fetch_queue_explain_rows(
        instance_id="bs1",
        current_screen="main_city",
        n=10,
        redis_url="redis://127.0.0.1:1/0",  # nothing listens on port 1
    )
    assert rows == []
