"""Focus mode suppresses the worker's autonomous enqueues.

When ``focus_scenario`` is set, the worker must not seed ``check_main_city`` or
enqueue the ``who_i_am`` identity probe — only the pinned scenario (popped via
the queue's focus filter) should run. These gates are what stop the fish-detect
Play button from also kicking off the "normal bot".
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import worker.instance_worker as instance_worker


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.removed: list[tuple[str, str]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True

    async def remove_by_task_type(self, task_type: str, instance_id: str) -> int:
        self.removed.append((task_type, instance_id))
        return 0


class _FakeRedisFocus:
    """Redis stub exposing hget + hmget over a single instance-state hash."""

    def __init__(self, fields: dict[str, str]) -> None:
        self._f = fields

    async def hget(self, _key: str, field: str) -> str | None:
        return self._f.get(field)

    async def hmget(self, _key: str, *fields: str) -> list[str | None]:
        return [self._f.get(f) for f in fields]


@pytest.mark.asyncio
async def test_startup_seed_skipped_when_focused() -> None:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._settings = SimpleNamespace(worker=SimpleNamespace())
    worker._queue = _FakeQueue()
    # Identified account that would normally seed check_main_city — but focused.
    worker._redis = _FakeRedisFocus(
        {"active_player": "401227964", "focus_scenario": "event.fishing_tournament"}
    )

    await instance_worker.InstanceWorker._seed_startup_tasks(worker)

    assert worker._queue.calls == []
    # Stale cleanup still runs.
    removed_types = {t for t, _ in worker._queue.removed}
    assert {"who_i_am", "check_main_city"} <= removed_types


@pytest.mark.asyncio
async def test_who_i_am_probe_skipped_when_focused() -> None:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._queue = _FakeQueue()
    # active_player empty would normally enqueue who_i_am; focus suppresses it.
    worker._redis = _FakeRedisFocus(
        {"active_player": "", "focus_scenario": "event.fishing_tournament"}
    )

    async def _screen() -> str:
        return "main_city"

    worker._instance_current_screen = _screen

    await worker._maybe_enqueue_who_i_am_when_active_player_missing()

    assert worker._queue.calls == []


@pytest.mark.asyncio
async def test_focus_scenario_helper_reads_flag() -> None:
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")
    worker._redis = _FakeRedisFocus({"focus_scenario": "event.fishing_tournament"})

    assert await worker._focus_scenario() == "event.fishing_tournament"

    worker._redis = _FakeRedisFocus({})
    assert await worker._focus_scenario() == ""

    worker._redis = None
    assert await worker._focus_scenario() == ""
