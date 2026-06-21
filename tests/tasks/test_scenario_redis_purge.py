"""Redis cleanup when scenarios are disabled from the UI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from dashboard.redis_client import purge_scenarios_from_redis

if TYPE_CHECKING:
    import redis


@pytest.mark.usefixtures("redis_sync")
def test_purge_removes_player_override_queue_and_push_ttl(redis_sync: redis.Redis) -> None:
    client = redis_sync
    client.set("wos:player:p1:scenario", "claim_trials")
    payload = json.dumps(
        {
            "task_id": "t1",
            "player_id": "p1",
            "task_type": "claim_trials",
            "priority": 10,
            "run_at": 1.0,
            "instance_id": "bs1",
        }
    )
    client.zadd("wos:queue:bs1", {payload: 1.0})
    client.set("wos:player:p1:push_ttl:claim_trials", "1", ex=60)
    client.set("wos:claimed:claim_trials", "p1")
    client.zadd("wos:instance:bs1:recent_runs", {"claim_trials|p1|abc": 100.0})

    result = purge_scenarios_from_redis(
        client,
        scenario_ids={"claim_trials"},
        player_ids=["p1"],
        instance_ids=["bs1"],
    )

    assert result.player_overrides_cleared == 1
    assert result.queue_items_removed == 1
    assert result.push_ttl_deleted == 1
    assert result.claims_deleted == 1
    assert result.recent_runs_pruned == 1
    assert client.get("wos:player:p1:scenario") is None
    assert client.zcard("wos:queue:bs1") == 0


@pytest.mark.usefixtures("redis_sync")
def test_purge_skips_instance_state_when_scenario_still_running(redis_sync: redis.Redis) -> None:
    client = redis_sync
    client.hset(
        "wos:instance:bs1:state",
        mapping={"current_scenario": "claim_trials", "current_task_type": "claim_trials"},
    )
    client.set(
        "wos:queue:running:bs1",
        json.dumps({"task_type": "claim_trials", "player_id": "p1"}),
    )

    result = purge_scenarios_from_redis(
        client,
        scenario_ids={"claim_trials"},
        player_ids=[],
        instance_ids=["bs1"],
    )

    assert result.instance_state_cleared == 0
    assert client.hget("wos:instance:bs1:state", "current_scenario") == "claim_trials"
