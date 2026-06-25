"""UI ``run now`` / ``reschedule`` must not re-queue an already-claimed task.

Both ``run_queue_task_now`` and ``reschedule_queue_task`` rescore a queued row
by ``ZREM`` (old payload) + ``ZADD`` (rewritten payload). If a worker's
``pop_due`` ``ZREM``s the same member between our read and our ``ZREM``, our
``ZREM`` returns 0 — the task is already running. Re-adding it (``ZADD``) would
re-queue a live task and cause a double execution. The helpers must treat
``zrem == 1`` as the claim, exactly like ``scheduler.queue.pop_due``.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from dashboard.redis_client import (
    _queue_key,
    reschedule_queue_task,
    run_queue_task_now,
)


def _payload(*, task_id: str, run_at: float, instance_id: str = "bs1") -> str:
    data: dict[str, Any] = {
        "task_id": task_id,
        "player_id": "",
        "task_type": "who_i_am",
        "priority": 50_000,
        "run_at": run_at,
        "instance_id": instance_id,
        "created_at": run_at,
    }
    return json.dumps(data, ensure_ascii=False)


class _ZremLoses:
    """Proxy that simulates losing the ZREM race.

    Delegates every call to the real client, but the first ``zrem`` actually
    removes the member (a peer worker claimed it) and *reports* 0 — the value a
    loser sees. Everything after is real, so a buggy helper's ``ZADD`` lands in
    the real queue and the test can catch it.
    """

    def __init__(self, real: Any) -> None:
        self._real = real
        self._zrem_calls = 0

    def zrem(self, key: str, *members: str) -> int:
        self._zrem_calls += 1
        if self._zrem_calls == 1:
            self._real.zrem(key, *members)  # peer already removed it
            return 0
        return int(self._real.zrem(key, *members))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


@pytest.mark.integration
def test_run_now_rescores_when_present(redis_sync: Any) -> None:
    r = redis_sync
    key = _queue_key("bs1")
    r.zadd(key, {_payload(task_id="t1", run_at=100.0): 100.0})

    assert run_queue_task_now(r, "t1") is True

    rows = r.zrangebyscore(key, "-inf", "+inf")
    assert len(rows) == 1, "rescore must leave exactly one row"
    data = json.loads(rows[0])
    assert data["task_id"] == "t1"
    assert data["run_at"] > 100.0, "run_at must be rewritten to ~now"


@pytest.mark.integration
def test_run_now_does_not_requeue_after_race_loss(redis_sync: Any) -> None:
    r = redis_sync
    key = _queue_key("bs1")
    r.zadd(key, {_payload(task_id="t1", run_at=100.0): 100.0})

    proxy = _ZremLoses(r)
    # ZREM returns 0 → a worker already popped t1 for execution.
    assert run_queue_task_now(proxy, "t1") is False  # type: ignore[arg-type]

    rows = r.zrangebyscore(key, "-inf", "+inf")
    assert rows == [], "must NOT re-queue a task a worker already claimed"


@pytest.mark.integration
def test_reschedule_rescores_when_present(redis_sync: Any) -> None:
    r = redis_sync
    key = _queue_key("bs1")
    r.zadd(key, {_payload(task_id="t1", run_at=100.0): 100.0})

    target = time.time() + 3600
    assert reschedule_queue_task(r, "t1", target) is True

    rows = r.zrange(key, 0, -1, withscores=True)
    assert len(rows) == 1
    member, score = rows[0]
    assert json.loads(member)["run_at"] == pytest.approx(target)
    assert score == pytest.approx(target)


@pytest.mark.integration
def test_reschedule_does_not_requeue_after_race_loss(redis_sync: Any) -> None:
    r = redis_sync
    key = _queue_key("bs1")
    r.zadd(key, {_payload(task_id="t1", run_at=100.0): 100.0})

    proxy = _ZremLoses(r)
    assert reschedule_queue_task(proxy, "t1", time.time() + 3600) is False  # type: ignore[arg-type]

    rows = r.zrangebyscore(key, "-inf", "+inf")
    assert rows == [], "must NOT re-queue a task a worker already claimed"
