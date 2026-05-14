"""``InstanceWorkerTasksMixin._reschedule_if_needed`` must forward
``resume_from_step_index`` from ``TaskResult.metadata`` into the
re-queued ``QueueItem`` as ``start_step_index``.

DSL scenarios that yield to higher-priority tasks return:
``TaskResult(next_run_at=now, metadata={"resume_from_step_index": N, ...})``
(see ``tasks/dsl_scenario_execute_mixin.py``). Without this forwarding,
the next run starts at step 0 and re-executes work that already
completed — e.g. ``claim_trials`` re-clicks days that were already
claimed.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from scheduler.queue import QueueItem
from tasks.base import TaskResult
from worker.instance_worker_tasks import InstanceWorkerTasksMixin


class _CaptureQueue:
    """Stub queue that records the kwargs passed to ``schedule``."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True


def _make_mixin(queue: _CaptureQueue) -> InstanceWorkerTasksMixin:
    obj = object.__new__(InstanceWorkerTasksMixin)
    obj._cfg = SimpleNamespace(instance_id="bs1")  # type: ignore[attr-defined]
    obj._redis = None  # type: ignore[attr-defined]
    obj._queue = queue  # type: ignore[attr-defined]
    return obj


def _qitem(**overrides: Any) -> QueueItem:
    defaults: dict[str, Any] = {
        "task_id": "t-1",
        "player_id": "p1",
        "task_type": "claim_trials",
        "priority": 50_000,
        "run_at": 0.0,
        "instance_id": "bs1",
    }
    defaults.update(overrides)
    return QueueItem(**defaults)


@pytest.mark.asyncio
async def test_reschedule_forwards_resume_from_step_index() -> None:
    queue = _CaptureQueue()
    mixin = _make_mixin(queue)
    item = _qitem()
    result = TaskResult(
        success=False,
        next_run_at=datetime.now(),
        metadata={
            "reason": "preempted_by_higher_priority",
            "preempted": True,
            "resume_from_step_index": 7,
        },
    )

    await mixin._reschedule_if_needed(item, result)  # type: ignore[attr-defined]

    assert len(queue.calls) == 1
    assert queue.calls[0]["start_step_index"] == 7
    assert queue.calls[0]["task_type"] == "claim_trials"
    assert queue.calls[0]["task_id"] == "t-1"


@pytest.mark.asyncio
async def test_reschedule_defaults_to_zero_when_no_resume_index() -> None:
    """Non-DSL reschedules (e.g. periodic) have no resume index — pass 0."""
    queue = _CaptureQueue()
    mixin = _make_mixin(queue)
    item = _qitem(task_type="periodic_check")
    result = TaskResult(
        success=True,
        next_run_at=datetime.now(),
        metadata={"reason": "ok"},
    )

    await mixin._reschedule_if_needed(item, result)  # type: ignore[attr-defined]

    assert len(queue.calls) == 1
    assert queue.calls[0]["start_step_index"] == 0


@pytest.mark.asyncio
async def test_reschedule_tolerates_garbage_resume_index() -> None:
    """Bad metadata shouldn't crash the reschedule path."""
    queue = _CaptureQueue()
    mixin = _make_mixin(queue)
    item = _qitem()
    result = TaskResult(
        success=False,
        next_run_at=datetime.now(),
        metadata={"resume_from_step_index": "not-an-int"},
    )

    await mixin._reschedule_if_needed(item, result)  # type: ignore[attr-defined]

    assert len(queue.calls) == 1
    assert queue.calls[0]["start_step_index"] == 0


@pytest.mark.asyncio
async def test_reschedule_clamps_negative_resume_index() -> None:
    queue = _CaptureQueue()
    mixin = _make_mixin(queue)
    item = _qitem()
    result = TaskResult(
        success=False,
        next_run_at=datetime.now(),
        metadata={"resume_from_step_index": -3},
    )

    await mixin._reschedule_if_needed(item, result)  # type: ignore[attr-defined]

    assert len(queue.calls) == 1
    assert queue.calls[0]["start_step_index"] == 0


@pytest.mark.asyncio
async def test_reschedule_skips_when_no_next_run_at() -> None:
    queue = _CaptureQueue()
    mixin = _make_mixin(queue)
    item = _qitem()
    result = TaskResult(success=True, next_run_at=None, metadata={})

    await mixin._reschedule_if_needed(item, result)  # type: ignore[attr-defined]

    assert queue.calls == []
