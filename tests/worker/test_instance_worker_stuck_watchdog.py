"""``InstanceWorker`` stuck-task watchdog: abort a task whose ``task.execute()``
has been blocked (e.g. on an unattended click-approval) past
``worker.stuck_task_abort_seconds``.

The worker deliberately skips ``asyncio.wait_for`` in approval mode so an
operator can take their time on a pending tap; the watchdog is the backstop that
keeps a *missed* approval from wedging the single task loop forever. See
``InstanceWorker._maybe_abort_stuck_task`` / ``_run_stuck_task_watchdog``.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from scheduler.queue import QueueItem
from tasks.base import TaskResult
from worker.instance_worker import InstanceWorker


def _make_worker(*, threshold: int) -> InstanceWorker:
    obj = object.__new__(InstanceWorker)
    obj._cfg = SimpleNamespace(instance_id="bs1")  # type: ignore[attr-defined]
    obj._settings = SimpleNamespace(  # type: ignore[attr-defined]
        worker=SimpleNamespace(
            stuck_task_abort_seconds=threshold,
            task_timeout_seconds=300,
        )
    )
    obj._current_task_handle = None  # type: ignore[attr-defined]
    obj._current_task_started_m = None  # type: ignore[attr-defined]
    obj._current_task_type = None  # type: ignore[attr-defined]
    return obj


async def _live_handle() -> asyncio.Task[None]:
    """A still-running task to stand in for a blocked ``task.execute()``."""
    return asyncio.ensure_future(asyncio.sleep(60))


@pytest.mark.asyncio
async def test_aborts_when_blocked_past_threshold() -> None:
    worker = _make_worker(threshold=10)
    handle = await _live_handle()
    worker._current_task_handle = handle  # type: ignore[attr-defined]
    worker._current_task_started_m = time.monotonic() - 999  # type: ignore[attr-defined]
    worker._current_task_type = "dismiss_unknown_popup"  # type: ignore[attr-defined]
    worker._cancel_current_task = AsyncMock(return_value=True)  # type: ignore[attr-defined]

    try:
        aborted = await worker._maybe_abort_stuck_task()
    finally:
        handle.cancel()

    assert aborted is True
    worker._cancel_current_task.assert_awaited_once()
    _, kwargs = worker._cancel_current_task.await_args
    assert kwargs["result_reason"] == "aborted_stuck"
    assert kwargs["reschedule"] is False


@pytest.mark.asyncio
async def test_no_abort_before_threshold() -> None:
    worker = _make_worker(threshold=900)
    handle = await _live_handle()
    worker._current_task_handle = handle  # type: ignore[attr-defined]
    worker._current_task_started_m = time.monotonic() - 5  # type: ignore[attr-defined]
    worker._cancel_current_task = AsyncMock(return_value=True)  # type: ignore[attr-defined]

    try:
        aborted = await worker._maybe_abort_stuck_task()
    finally:
        handle.cancel()

    assert aborted is False
    worker._cancel_current_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_when_threshold_zero() -> None:
    worker = _make_worker(threshold=0)
    handle = await _live_handle()
    worker._current_task_handle = handle  # type: ignore[attr-defined]
    worker._current_task_started_m = time.monotonic() - 999  # type: ignore[attr-defined]
    worker._cancel_current_task = AsyncMock(return_value=True)  # type: ignore[attr-defined]

    try:
        aborted = await worker._maybe_abort_stuck_task()
    finally:
        handle.cancel()

    assert aborted is False
    worker._cancel_current_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_abort_when_idle() -> None:
    worker = _make_worker(threshold=10)
    worker._cancel_current_task = AsyncMock(return_value=True)  # type: ignore[attr-defined]

    aborted = await worker._maybe_abort_stuck_task()

    assert aborted is False
    worker._cancel_current_task.assert_not_awaited()


class _BlockingTask:
    """Stub ``BaseTask`` whose ``execute`` blocks until cancelled."""

    is_cooperative = False
    scenario_key = ""
    task_type = "dismiss_unknown_popup"

    async def execute(self, _instance_id: str) -> TaskResult:
        await asyncio.Event().wait()  # never set → blocks like a pending approval
        msg = "unreachable"
        raise AssertionError(msg)


@pytest.mark.asyncio
async def test_watchdog_abort_yields_aborted_stuck_result(monkeypatch: Any) -> None:
    """End-to-end: a blocked task aborted by the watchdog comes back as a clean
    ``TaskResult(success=False, reason="aborted_stuck")`` so the queue moves on."""
    import worker.instance_worker as iw

    # Approval mode ON → the worker skips ``asyncio.wait_for``, so only the
    # watchdog can end the blocked task.
    monkeypatch.setattr(iw, "click_approval_enabled", lambda _iid: True)
    monkeypatch.setattr(iw, "capture_interval_s_for_scenario_key", lambda *_a: None)
    monkeypatch.setattr(iw, "repo_root", lambda: ".")

    worker = _make_worker(threshold=10)
    # Real ``_cancel_current_task`` runs, but stub the approval-abort plumbing.
    worker._run_blocking = AsyncMock(return_value=None)  # type: ignore[attr-defined]
    worker._redis = None  # type: ignore[attr-defined]
    worker._task_aborted_for_restart = False  # type: ignore[attr-defined]
    worker._task_abort_result_reason = "aborted_for_restart"  # type: ignore[attr-defined]
    worker._task_abort_reschedule = False  # type: ignore[attr-defined]

    item = QueueItem(
        task_id="t-1",
        player_id="p1",
        task_type="dismiss_unknown_popup",
        priority=70_000,
        run_at=0.0,
        instance_id="bs1",
    )
    exec_fut = asyncio.ensure_future(worker._execute_task(item, _BlockingTask()))

    # Wait until the task is registered as running, then backdate its start so
    # the watchdog trips on the next tick.
    for _ in range(100):
        if worker._current_task_handle is not None:
            break
        await asyncio.sleep(0.01)
    assert worker._current_task_handle is not None
    worker._current_task_started_m = time.monotonic() - 999  # type: ignore[attr-defined]

    aborted = await worker._maybe_abort_stuck_task()
    assert aborted is True

    result = await asyncio.wait_for(exec_fut, timeout=2.0)
    assert isinstance(result, TaskResult)
    assert result.success is False
    assert result.metadata.get("reason") == "aborted_stuck"
    # The approval-abort signal was stamped before cancelling the handle.
    worker._run_blocking.assert_awaited()
