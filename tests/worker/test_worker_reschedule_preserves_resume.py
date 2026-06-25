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

from datetime import UTC, datetime
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
        next_run_at=datetime.now(tz=UTC),
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
        next_run_at=datetime.now(tz=UTC),
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
        next_run_at=datetime.now(tz=UTC),
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
        next_run_at=datetime.now(tz=UTC),
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


@pytest.mark.asyncio
async def test_reschedule_forwards_full_payload() -> None:
    """A rescheduled item must carry its WHOLE payload, not just the ranking
    subset. The generic ``task_type="dsl_scenario"`` envelope is meaningless
    without ``dsl_scenario``, and a planner task loses its reservation/quota
    markers + hero/troop assignment if ``args`` is dropped — so after a
    preemption/deferral it would come back as a scenario-less or
    reservation-less husk.
    """
    queue = _CaptureQueue()
    mixin = _make_mixin(queue)
    item = _qitem(
        task_type="dsl_scenario",
        dsl_scenario="intel_run",
        args={
            "resource_reservation": "intel:170",
            "resource_action_id": "intel",
            "resource_period": "20260626",
            "assign_heroes": ["molly"],
            "stamina_delta": -10,
        },
        region="board.intel",
        tap_x_pct=12.5,
        tap_y_pct=33.0,
        set_node="intel",
        threshold=0.9,
        score=0.97,
        match_top_left_x=4,
        match_top_left_y=5,
        template_w=20,
        template_h=10,
        tap_match_x_pct=13.0,
        tap_match_y_pct=34.0,
    )
    result = TaskResult(
        success=False,
        next_run_at=datetime.now(tz=UTC),
        metadata={"reason": "preempted_by_higher_priority", "resume_from_step_index": 3},
    )

    await mixin._reschedule_if_needed(item, result)  # type: ignore[attr-defined]

    assert len(queue.calls) == 1
    call = queue.calls[0]
    # The two fields whose loss is dangerous (the user's emphasis):
    assert call["dsl_scenario"] == "intel_run"
    assert call["args"] == {
        "resource_reservation": "intel:170",
        "resource_action_id": "intel",
        "resource_period": "20260626",
        "assign_heroes": ["molly"],
        "stamina_delta": -10,
    }
    # ...and the rest of the payload that used to be silently dropped.
    assert call["set_node"] == "intel"
    assert call["region"] == "board.intel"
    assert (call["tap_x_pct"], call["tap_y_pct"]) == (12.5, 33.0)
    assert (call["threshold"], call["score"]) == (0.9, 0.97)
    assert (call["match_top_left_x"], call["match_top_left_y"]) == (4, 5)
    assert (call["template_w"], call["template_h"]) == (20, 10)
    assert (call["tap_match_x_pct"], call["tap_match_y_pct"]) == (13.0, 34.0)
    # Resume index still forwarded alongside the payload.
    assert call["start_step_index"] == 3


@pytest.mark.asyncio
async def test_reschedule_uses_skip_if_duplicate() -> None:
    """Reschedule must dedupe against any same-signature item already queued.

    Without this, two device-level scenarios with the same
    ``(instance, task_type, player, region)`` (e.g. an orphaned ``who_i_am``
    plus the running one's yield) end up in the queue simultaneously. Because
    device-level tasks bypass the priority-gap gate in
    ``_preempted_by_higher_priority`` (``top_is_device_level`` branch), each
    yields to the other and no progress is made — the symptom is an exploding
    ``yield_count:*`` set under a single instance.
    """
    queue = _CaptureQueue()
    mixin = _make_mixin(queue)
    item = _qitem(task_type="who_i_am", player_id="")
    result = TaskResult(
        success=False,
        next_run_at=datetime.now(tz=UTC),
        metadata={"reason": "preempted_by_higher_priority"},
    )

    await mixin._reschedule_if_needed(item, result)  # type: ignore[attr-defined]

    assert len(queue.calls) == 1
    assert queue.calls[0]["skip_if_duplicate"] is True
