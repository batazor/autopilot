from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from scheduler.runner import SchedulerRunner


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True

    async def peek_all(self) -> list[dict[str, Any]]:
        return []


@pytest.mark.asyncio
async def test_scheduler_does_not_enqueue_duplicate_assignments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: ``SchedulerRunner._run_once`` must enqueue optimizer-assigned
    tasks with ``skip_if_duplicate=True`` and ``dedup_ignore_region=True``.

    Without those flags, repeated scheduler ticks accumulate duplicates of the
    same logical task in the queue when the worker has not popped them yet.
    """

    runner = SchedulerRunner()
    runner._queue = _FakeQueue()  # type: ignore[assignment]
    runner._redis = SimpleNamespace()  # type: ignore[assignment]

    async def _no_cron() -> None:
        return None

    async def _player_states() -> dict[str, dict[str, object]]:
        return {"p1": {"player_id": "p1"}}

    async def _player_instance_map() -> dict[str, str]:
        return {"p1": "bs1"}

    runner._run_cron_specs = _no_cron  # type: ignore[assignment]
    runner._load_player_states = _player_states  # type: ignore[assignment]
    runner._build_player_instance_map = _player_instance_map  # type: ignore[assignment]

    monkeypatch.setattr(runner._scenario_loader, "load_all", lambda: [])

    async def _active_scenario_id(player_id: str) -> str | None:
        return None

    runner._active_scenario_id = _active_scenario_id  # type: ignore[assignment]

    fake_task = SimpleNamespace(
        task_id="t1",
        task_type="check_main_city",
        priority=10,
    )

    async def _fake_executor(_loop: Any, _func: Any, _inp: Any) -> dict[str, list[Any]]:
        return {"p1": [fake_task]}

    monkeypatch.setattr("scheduler.runner.run_in_ortools_executor", _fake_executor)

    await runner._run_once()

    assert len(runner._queue.calls) == 1, runner._queue.calls  # type: ignore[union-attr]
    call = runner._queue.calls[0]  # type: ignore[union-attr]
    assert call["skip_if_duplicate"] is True
    assert call["dedup_ignore_region"] is True
    assert call["task_id"] == "t1"
    assert call["player_id"] == "p1"
    assert call["instance_id"] == "bs1"
