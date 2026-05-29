from __future__ import annotations

import asyncio
from typing import Any

import pytest

from worker import async_supervisor


@pytest.fixture(autouse=True)
def _stub_settings(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Force a small base delay so tests don't sleep forever."""
    class _Worker:
        restart_wait_seconds = 1

    class _Settings:
        worker = _Worker()

    settings = _Settings()
    monkeypatch.setattr(async_supervisor, "get_settings", lambda: settings)
    return settings


@pytest.mark.asyncio
async def test_guard_loop_increments_attempt_on_quick_crash(
    monkeypatch: pytest.MonkeyPatch,
    _stub_settings: Any,
) -> None:
    """Repeated quick crashes must produce monotonically growing delays."""

    sleeps: list[float] = []

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(async_supervisor.asyncio, "sleep", _no_sleep)
    # Zero jitter so the doubling is testable.
    monkeypatch.setattr(
        async_supervisor,
        "compute_restart_delay",
        lambda attempt, *, base_seconds: base_seconds * (2 ** (attempt - 1)),
    )

    async def _crash() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(asyncio.CancelledError):
        await async_supervisor._guard_loop("test", _crash, settings=_stub_settings)

    # base=1 → 1, 2, 4 for attempts 1, 2, 3
    assert sleeps == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_guard_loop_resets_attempt_after_stable_run(
    monkeypatch: pytest.MonkeyPatch,
    _stub_settings: Any,
) -> None:
    """A child that ran past the stability window resets back to attempt=1."""

    sleeps: list[float] = []
    monotonic_values = [
        0.0,    # iter 1: start
        0.1,    # iter 1: crash (ran 0.1s → not stable, attempt=1)
        1.0,    # iter 2: start (after sleep)
        400.0,  # iter 2: crash (ran 399s > 300 window → stable, attempt reset to 1)
        500.0,  # iter 3: start
        500.1,  # iter 3: crash (ran 0.1s → attempt=2)
    ]
    idx = {"i": 0}

    def _monotonic() -> float:
        i = idx["i"]
        # Saturate at the final value so any extra calls during teardown
        # don't blow up the test with StopIteration.
        v = monotonic_values[min(i, len(monotonic_values) - 1)]
        idx["i"] = i + 1
        return v

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(async_supervisor.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(async_supervisor.time, "monotonic", _monotonic)
    monkeypatch.setattr(
        async_supervisor,
        "compute_restart_delay",
        lambda attempt, *, base_seconds: base_seconds * (2 ** (attempt - 1)),
    )

    async def _crash() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(asyncio.CancelledError):
        await async_supervisor._guard_loop("test", _crash, settings=_stub_settings)

    # base=1; attempt sequence is 1 (quick), then reset to 1 (after stable),
    # then 2 (quick again). Delays: 1, 1, 2.
    assert sleeps == [1.0, 1.0, 2.0]


@pytest.mark.asyncio
async def test_guard_loop_escalates_when_crash_period_below_window(
    monkeypatch: pytest.MonkeyPatch,
    _stub_settings: Any,
) -> None:
    """A worker that reliably crashes ~45s in must still escalate its backoff.

    Regression: the stability window used to be ``base * 4`` (~4s here / 40s in
    prod), so a process dying just past it reset to attempt=1 every cycle and
    never backed off. The window is now an absolute 300s, so sub-window crash
    loops escalate.
    """

    sleeps: list[float] = []
    # Each run lasts ~45s (start, crash) — below the 300s window, so no reset.
    monotonic_values = [
        0.0, 45.0,     # iter 1: ran 45s
        100.0, 145.0,  # iter 2: ran 45s
        200.0, 245.0,  # iter 3: ran 45s
    ]
    idx = {"i": 0}

    def _monotonic() -> float:
        i = idx["i"]
        v = monotonic_values[min(i, len(monotonic_values) - 1)]
        idx["i"] = i + 1
        return v

    async def _no_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(async_supervisor.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(async_supervisor.time, "monotonic", _monotonic)
    monkeypatch.setattr(
        async_supervisor,
        "compute_restart_delay",
        lambda attempt, *, base_seconds: base_seconds * (2 ** (attempt - 1)),
    )

    async def _crash() -> None:
        msg = "boom"
        raise RuntimeError(msg)

    with pytest.raises(asyncio.CancelledError):
        await async_supervisor._guard_loop("test", _crash, settings=_stub_settings)

    # base=1; attempts 1, 2, 3 → delays double instead of staying flat at 1.
    assert sleeps == [1.0, 2.0, 4.0]


@pytest.mark.asyncio
async def test_guard_loop_propagates_cancellation(
    monkeypatch: pytest.MonkeyPatch,
    _stub_settings: Any,
) -> None:
    """A CancelledError from the inner task must not be swallowed by the loop."""

    async def _cancelled() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await async_supervisor._guard_loop("test", _cancelled, settings=_stub_settings)
