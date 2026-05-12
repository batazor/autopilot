"""Regression: a task left in the 'running' slot after worker crash must be
failed at boot, not left hanging.

``wos:queue:running:<instance_id>`` is published when ``_run_one_queue_item``
starts a task and deleted in its ``finally``. If the worker dies mid-task,
the key survives (until the 180s TTL) and the UI keeps rendering the dead
task as still executing — meanwhile the ``QueueItem`` is gone from the
sorted set and nothing re-enqueues it.

These tests pin ``_fail_stuck_running_on_boot`` to:
1. Write a history entry marked ``success=false``.
2. Delete the running key.
3. Clear ``current_task_*`` / ``current_scenario`` fields in the state hash.
4. No-op cleanly when there's nothing to clean, or when Redis isn't wired.
5. Scope to ``self._cfg.instance_id`` only.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

import worker.instance_worker as instance_worker


def _make_worker(redis_client: object, instance_id: str = "bs1") -> object:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id=instance_id)
    worker._redis = redis_client
    return worker


@pytest.mark.asyncio
async def test_fail_stuck_running_writes_history_and_clears_state(
    redis_async: object,
) -> None:
    r = redis_async
    running_key = "wos:queue:running:bs1"
    state_key = "wos:instance:bs1:state"
    history_key = "wos:queue:history:bs1"

    started_at = time.time() - 56.0
    payload = {
        "task_id": "ovl:bs1:new_chapter:1a0d6aad",
        "task_type": "new_chapter",
        "player_id": "765502864",
        "priority": 70000,
        "instance_id": "bs1",
        "region": "chapter.new",
        "started_at": started_at,
    }
    await r.set(running_key, json.dumps(payload))  # type: ignore[attr-defined]
    await r.hset(  # type: ignore[attr-defined]
        state_key,
        mapping={
            "current_task_player": "765502864",
            "current_task_started_at": str(started_at),
            "current_task_region": "chapter.new",
            "current_scenario": "new_chapter",
            "last_overlay_match_score": "0.92",
        },
    )

    worker = _make_worker(r)
    await instance_worker.InstanceWorker._fail_stuck_running_on_boot(worker)

    assert await r.get(running_key) is None  # type: ignore[attr-defined]

    state = await r.hgetall(state_key)  # type: ignore[attr-defined]
    for field in (
        "current_task_player",
        "current_task_started_at",
        "current_task_region",
        "current_scenario",
        "last_overlay_match_score",
    ):
        assert state.get(field, "") == "", f"{field} should be cleared, got {state.get(field)!r}"

    history_raw = await r.lrange(history_key, 0, -1)  # type: ignore[attr-defined]
    assert len(history_raw) == 1
    row = json.loads(history_raw[0])
    assert row["task_id"] == "ovl:bs1:new_chapter:1a0d6aad"
    assert row["task_type"] == "new_chapter"
    assert row["player_id"] == "765502864"
    assert row["region"] == "chapter.new"
    assert row["instance_id"] == "bs1"
    assert row["success"] is False
    assert row["error"] == "worker restarted mid-task"
    assert row["reason"] == "worker_restart"
    assert row["started_at"] == pytest.approx(started_at, abs=0.01)
    assert row["duration_s"] >= 56.0


@pytest.mark.asyncio
async def test_fail_stuck_running_no_op_when_no_running_key(
    redis_async: object,
) -> None:
    """Clean boot — no running key means no history entry, no errors."""
    worker = _make_worker(redis_async)
    await instance_worker.InstanceWorker._fail_stuck_running_on_boot(worker)

    history = await redis_async.lrange("wos:queue:history:bs1", 0, -1)  # type: ignore[attr-defined]
    assert history == []


@pytest.mark.asyncio
async def test_fail_stuck_running_no_op_when_redis_unset(
    redis_async: object,
) -> None:
    """``self._redis is None`` early in setup → cleanup is a no-op (must not raise)."""
    worker = _make_worker(None)
    await instance_worker.InstanceWorker._fail_stuck_running_on_boot(worker)


@pytest.mark.asyncio
async def test_fail_stuck_running_only_touches_own_instance(
    redis_async: object,
) -> None:
    """Each worker owns its own per-instance keys — bs2's stale slot must survive."""
    r = redis_async
    payload_bs1 = json.dumps({"task_id": "t1", "task_type": "x", "started_at": time.time()})
    payload_bs2 = json.dumps({"task_id": "t2", "task_type": "y", "started_at": time.time()})
    await r.set("wos:queue:running:bs1", payload_bs1)  # type: ignore[attr-defined]
    await r.set("wos:queue:running:bs2", payload_bs2)  # type: ignore[attr-defined]
    await r.hset("wos:instance:bs2:state", "current_scenario", "y")  # type: ignore[attr-defined]

    worker = _make_worker(r, instance_id="bs1")
    await instance_worker.InstanceWorker._fail_stuck_running_on_boot(worker)

    assert await r.get("wos:queue:running:bs1") is None  # type: ignore[attr-defined]
    assert await r.get("wos:queue:running:bs2") is not None  # type: ignore[attr-defined]
    bs2_state = await r.hgetall("wos:instance:bs2:state")  # type: ignore[attr-defined]
    assert bs2_state.get("current_scenario") == "y"


@pytest.mark.asyncio
async def test_fail_stuck_running_tolerates_malformed_payload(
    redis_async: object,
) -> None:
    """A junk payload must not block cleanup: still delete the running key and
    write a best-effort history entry. Otherwise a single corrupt write would
    permanently wedge boot for that instance."""
    r = redis_async
    await r.set("wos:queue:running:bs1", "not-json{")  # type: ignore[attr-defined]

    worker = _make_worker(r)
    await instance_worker.InstanceWorker._fail_stuck_running_on_boot(worker)

    assert await r.get("wos:queue:running:bs1") is None  # type: ignore[attr-defined]
    history = await r.lrange("wos:queue:history:bs1", 0, -1)  # type: ignore[attr-defined]
    assert len(history) == 1
    row = json.loads(history[0])
    assert row["success"] is False
    assert row["error"] == "worker restarted mid-task"
