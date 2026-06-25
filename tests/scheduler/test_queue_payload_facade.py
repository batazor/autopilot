"""The shared queue-payload facade (``scheduler.queue_payload``).

``RedisQueue.schedule`` (async), the notify publisher, and the optimizer
dispatcher all enqueue through this one seam now, so the payload shape, the
atomic Lua dedup, and the dashboard ``queue/enqueue`` event can't drift between
them. These tests pin:

* :func:`build_queue_body` — the canonical field set + coercions;
* :func:`effective_task_type` — the scenario identity used by dedup;
* :func:`enqueue_sync` — write + publish + return contract, and that dedup keys
  on the *effective* task type (so a ``task_type="dsl_scenario"`` envelope dedups
  against a cron push of the same scenario, and two different scenarios sharing
  the generic envelope are NOT collapsed).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from scheduler.queue_payload import (
    build_queue_body,
    effective_task_type,
    enqueue_sync,
    queue_key,
)

if TYPE_CHECKING:
    import redis


def _members(client: redis.Redis, instance_id: str) -> list[dict]:
    raw = client.zrangebyscore(queue_key(instance_id), "-inf", "+inf")
    return [json.loads(m) for m in raw]


# --- build_queue_body (pure) -------------------------------------------------


def test_build_queue_body_carries_every_set_field() -> None:
    body = build_queue_body(
        task_id="t1",
        player_id="p1",
        task_type="overlay_tap",
        priority=100,
        run_at=1_700_000_000.0,
        instance_id="bs1",
        region="workers",
        score=0.91,
        set_node="main_city",
        dsl_scenario="",  # empty string is omitted, not stored
        args={"a": 1},
        start_step_index=3,
        expires_at=1_700_000_900.0,
        created_at=123.0,
    )
    assert body["region"] == "workers"
    # score aliases to overlay_match_score (pop_due prefers the alias first)
    assert body["score"] == 0.91
    assert body["overlay_match_score"] == 0.91
    assert body["set_node"] == "main_city"
    assert "dsl_scenario" not in body
    assert body["args"] == {"a": 1}
    assert body["start_step_index"] == 3
    assert body["expires_at"] == 1_700_000_900.0
    assert body["created_at"] == 123.0


def test_build_queue_body_omits_unset_optionals_and_stamps_created_at() -> None:
    body = build_queue_body(
        task_id="t",
        player_id="",
        task_type="cron_task",
        priority=1,
        run_at=1.0,
        instance_id="bs1",
    )
    assert set(body) == {
        "task_id",
        "player_id",
        "task_type",
        "priority",
        "run_at",
        "instance_id",
        "created_at",
    }
    assert isinstance(body["created_at"], float)  # defaulted to time.time()


def test_effective_task_type_resolves_the_dsl_scenario_envelope() -> None:
    assert effective_task_type("claim_mail", None) == "claim_mail"
    assert effective_task_type("dsl_scenario", "claim_mail") == "claim_mail"
    # generic envelope with no key falls back to the literal transport type
    assert effective_task_type("dsl_scenario", "") == "dsl_scenario"


# --- enqueue_sync: write / publish / return (mock client) --------------------


def test_enqueue_sync_writes_and_publishes_dashboard_event() -> None:
    client = MagicMock()
    with patch("dashboard.dashboard_events.publish_dashboard_event") as pub:
        ok = enqueue_sync(
            client,
            task_id="t1",
            player_id="p1",
            task_type="dsl_scenario",
            priority=50_000,
            run_at=1.0,
            instance_id="bs1",
            dsl_scenario="claim_mail",
        )
    assert ok is True
    client.zadd.assert_called_once()
    pub.assert_called_once_with(
        client, topic="queue", instance_id="bs1", reason="enqueue"
    )


def test_enqueue_sync_dedup_skip_returns_false_without_publish() -> None:
    client = MagicMock()
    client.eval.return_value = 0  # Lua found a duplicate, skipped the ZADD
    with patch("dashboard.dashboard_events.publish_dashboard_event") as pub:
        ok = enqueue_sync(
            client,
            task_id="t1",
            player_id="p1",
            task_type="dsl_scenario",
            priority=50_000,
            run_at=1.0,
            instance_id="bs1",
            dsl_scenario="claim_mail",
            skip_if_duplicate=True,
        )
    assert ok is False
    client.eval.assert_called_once()
    client.zadd.assert_not_called()
    pub.assert_not_called()


# --- enqueue_sync dedup semantics (real Redis) -------------------------------


def test_enqueue_sync_dedup_keys_on_effective_task_type(redis_sync: redis.Redis) -> None:
    """Two generic ``dsl_scenario`` envelopes with DIFFERENT scenario keys must
    both survive — they only share the transport ``task_type``, not identity."""
    a = enqueue_sync(
        redis_sync, task_id="a", player_id="p1", task_type="dsl_scenario",
        priority=1, run_at=1_700_000_000.0, instance_id="bs1",
        dsl_scenario="claim_mail", skip_if_duplicate=True, dedup_ignore_region=True,
    )
    b = enqueue_sync(
        redis_sync, task_id="b", player_id="p1", task_type="dsl_scenario",
        priority=1, run_at=1_700_000_001.0, instance_id="bs1",
        dsl_scenario="claim_trials", skip_if_duplicate=True, dedup_ignore_region=True,
    )
    assert a is True
    assert b is True
    items = _members(redis_sync, "bs1")
    assert sorted(i["dsl_scenario"] for i in items) == ["claim_mail", "claim_trials"]


def test_enqueue_sync_dedup_collapses_same_scenario(redis_sync: redis.Redis) -> None:
    first = enqueue_sync(
        redis_sync, task_id="a", player_id="p1", task_type="dsl_scenario",
        priority=1, run_at=1_700_000_000.0, instance_id="bs1",
        dsl_scenario="claim_mail", skip_if_duplicate=True, dedup_ignore_region=True,
    )
    second = enqueue_sync(
        redis_sync, task_id="b", player_id="p1", task_type="dsl_scenario",
        priority=1, run_at=1_700_000_001.0, instance_id="bs1",
        dsl_scenario="claim_mail", skip_if_duplicate=True, dedup_ignore_region=True,
    )
    assert first is True
    assert second is False
    items = _members(redis_sync, "bs1")
    assert len(items) == 1
    assert items[0]["task_id"] == "a"


def test_enqueue_sync_dedup_is_cross_producer(redis_sync: redis.Redis) -> None:
    """A cron-style push (``task_type``=scenario key) and a notify/optimizer-style
    push (``task_type="dsl_scenario"`` + ``dsl_scenario`` key) for the same
    scenario+player are one logical task; the second must collapse."""
    cron = enqueue_sync(
        redis_sync, task_id="cron", player_id="p1", task_type="claim_mail",
        priority=1, run_at=1_700_000_000.0, instance_id="bs1",
        skip_if_duplicate=True, dedup_ignore_region=True,
    )
    notify = enqueue_sync(
        redis_sync, task_id="notify", player_id="p1", task_type="dsl_scenario",
        priority=1, run_at=1_700_000_001.0, instance_id="bs1",
        dsl_scenario="claim_mail", skip_if_duplicate=True, dedup_ignore_region=True,
    )
    assert cron is True
    assert notify is False
    assert len(_members(redis_sync, "bs1")) == 1


def test_enqueue_sync_without_dedup_always_writes(redis_sync: redis.Redis) -> None:
    for i in range(2):
        ok = enqueue_sync(
            redis_sync, task_id=f"t{i}", player_id="p1", task_type="dsl_scenario",
            priority=1, run_at=1_700_000_000.0 + i, instance_id="bs1",
            dsl_scenario="claim_mail", skip_if_duplicate=False,
        )
        assert ok is True
    assert len(_members(redis_sync, "bs1")) == 2
