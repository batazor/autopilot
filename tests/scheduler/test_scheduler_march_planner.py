"""The scheduler's autonomous MARCH planner (`_run_march_planner`).

Verifies the glue: gated by coordinator/march.yaml `enabled`, it reads idle
march slots from the resource world and dispatches the coordinator's MARCH
winners (intel_run / timed events) — without the resource planner being enabled.
Uses fakes for Redis + the queue (no testcontainer needed).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from games.wos.core.coordinator import dispatch as march_dispatch

from scheduler.runner import SchedulerRunner

if TYPE_CHECKING:
    from config.loader import Settings


def _make_scheduler_runner(settings: Settings) -> SchedulerRunner:
    return SchedulerRunner(settings)


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def schedule(self, **kwargs: Any) -> bool:
        self.calls.append(kwargs)
        return True

    async def last_run_at(self, **kwargs: Any) -> float | None:
        return None


class _FakeRedis:
    """Only the surface the march planner touches: read_ledger's hgetall."""

    async def hgetall(self, key: str) -> dict[str, str]:
        return {}  # no held reservations


def _enable(monkeypatch: pytest.MonkeyPatch, *, enabled: bool) -> None:
    monkeypatch.setattr(
        march_dispatch,
        "load_march_config",
        lambda: march_dispatch.MarchConfig(enabled=enabled, intel_cooldown_s=900),
    )


@pytest.mark.asyncio
async def test_march_planner_dispatches_intel_when_enabled(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _make_scheduler_runner(settings)
    queue = _FakeQueue()
    runner._queue = queue  # type: ignore[assignment]
    runner._redis = _FakeRedis()  # type: ignore[assignment]
    _enable(monkeypatch, enabled=True)

    # 5 free march slots (capacity read by sync_marching_status) + stamina.
    states = {
        "p1": {"stamina": "100", "marches.capacity": "5", "marches.active_count": "0"}
    }
    await runner._run_march_planner(states, {"p1": "bs1"}, 10_000.0)

    assert any(c["task_type"] == "intel_run" for c in queue.calls)
    assert all(c["instance_id"] == "bs1" and c["player_id"] == "p1" for c in queue.calls)


@pytest.mark.asyncio
async def test_march_planner_dormant_when_disabled(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = _make_scheduler_runner(settings)
    queue = _FakeQueue()
    runner._queue = queue  # type: ignore[assignment]
    runner._redis = _FakeRedis()  # type: ignore[assignment]
    _enable(monkeypatch, enabled=False)

    await runner._run_march_planner(
        {"p1": {"stamina": "100", "marches.capacity": "5"}}, {"p1": "bs1"}, 10_000.0
    )

    assert queue.calls == []


@pytest.mark.asyncio
async def test_march_planner_passes_player_role(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-account role (planner.role) reaches the coordinator — without it
    every account ran the balanced default and the role mechanism was inert."""
    runner = _make_scheduler_runner(settings)
    runner._queue = _FakeQueue()  # type: ignore[assignment]
    runner._redis = _FakeRedis()  # type: ignore[assignment]
    _enable(monkeypatch, enabled=True)

    captured: dict[str, Any] = {}

    async def _spy(**kwargs: Any) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(march_dispatch, "run_march_tick", _spy)

    states = {
        "p1": {"stamina": "100", "marches.capacity": "5", "planner.role": "farm"}
    }
    await runner._run_march_planner(states, {"p1": "bs1"}, 10_000.0)

    from games.wos.core.roles import get_role

    assert captured["role"] == get_role("farm")
    assert captured["role"].id == "farm"


def test_apply_durable_role_mirrors_from_store(monkeypatch: pytest.MonkeyPatch) -> None:
    from scheduler.runner import _apply_durable_role

    class _Gamer:
        def get(self, key: str, default: Any = None) -> Any:
            return "fighter" if key == "planner.role" else default

    class _Store:
        def get(self, _fid: str) -> _Gamer:
            return _Gamer()

    monkeypatch.setattr("config.state_store.get_state_store", lambda: _Store())
    state: dict[str, object] = {}
    _apply_durable_role("p1", state)
    assert state["planner.role"] == "fighter"


def test_apply_durable_role_noop_when_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    from scheduler.runner import _apply_durable_role

    opened: list[int] = []
    monkeypatch.setattr(
        "config.state_store.get_state_store", lambda: opened.append(1)  # type: ignore[func-returns-value]
    )
    state: dict[str, object] = {"planner.role": "farm"}
    _apply_durable_role("p1", state)
    assert state["planner.role"] == "farm"
    assert opened == []  # short-circuited; the store was never opened
