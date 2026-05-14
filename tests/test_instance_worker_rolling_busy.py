"""Rolling-tick busy gates.

Pre-fix: ``_device_reference_snapshot_tick`` always ran ADB screencap →
screen detect → (maybe overlay) every ``device_reference_snapshot_interval_seconds``,
even while a task was busy. With ``overlay_analyze_when_busy=False`` only
the overlay step was skipped — the screen detect (a non-trivial OpenCV
template match) and the snapshot cadence stayed at idle pace.

Post-fix: while a task is busy we
* run the loop at ``device_reference_snapshot_busy_interval_seconds`` instead
  of the idle interval — preview still updates for the UI watcher, just
  less often;
* skip screen detect by default (``screen_detect_when_busy=False``) — the
  scenario already knows what screen it's on, and the post-task
  ``_overlay_tick_now`` will re-detect on a fresh frame as soon as the
  task finishes.

The flags exist so operators can opt back into the old behavior per
workload (debugging, dense overlay rules, etc.).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from worker.instance_worker_rolling import (
    InstanceWorkerRollingMixin,
    _rolling_should_skip_overlay,
    _rolling_should_skip_screen_detect,
    _rolling_snapshot_interval,
)


@dataclass
class _Cfg:
    overlay_analyze_when_busy: bool = False
    screen_detect_when_busy: bool = False
    device_reference_snapshot_interval_seconds: float = 1.0
    device_reference_snapshot_busy_interval_seconds: float = 5.0


# ---------------------------------------------------------------------------
# Pure gate helpers
# ---------------------------------------------------------------------------


def test_snapshot_interval_idle_vs_busy() -> None:
    cfg = _Cfg(
        device_reference_snapshot_interval_seconds=1.0,
        device_reference_snapshot_busy_interval_seconds=5.0,
    )
    assert _rolling_snapshot_interval(cfg, task_busy=False) == pytest.approx(1.0)
    assert _rolling_snapshot_interval(cfg, task_busy=True) == pytest.approx(5.0)


def test_snapshot_interval_busy_equals_idle_restores_old_behavior() -> None:
    """Operator who wants the historical "always idle cadence" sets both
    intervals equal; the helper returns the same value in both states."""
    cfg = _Cfg(
        device_reference_snapshot_interval_seconds=2.0,
        device_reference_snapshot_busy_interval_seconds=2.0,
    )
    assert _rolling_snapshot_interval(cfg, task_busy=False) == 2.0
    assert _rolling_snapshot_interval(cfg, task_busy=True) == 2.0


@pytest.mark.parametrize(
    "task_busy,flag,expected",
    [
        # Idle never skips, regardless of flag.
        (False, False, False),
        (False, True, False),
        # Busy + flag=False → skip.
        (True, False, True),
        # Busy + flag=True → operator opted into "keep running while busy".
        (True, True, False),
    ],
)
def test_skip_screen_detect_gate(task_busy: bool, flag: bool, expected: bool) -> None:
    assert (
        _rolling_should_skip_screen_detect(
            _Cfg(screen_detect_when_busy=flag), task_busy=task_busy
        )
        is expected
    )


@pytest.mark.parametrize(
    "task_busy,flag,expected",
    [
        (False, False, False),
        (False, True, False),
        (True, False, True),
        (True, True, False),
    ],
)
def test_skip_overlay_gate(task_busy: bool, flag: bool, expected: bool) -> None:
    assert (
        _rolling_should_skip_overlay(
            _Cfg(overlay_analyze_when_busy=flag), task_busy=task_busy
        )
        is expected
    )


# ---------------------------------------------------------------------------
# Full _device_reference_snapshot_tick integration
# ---------------------------------------------------------------------------


class _Harness(InstanceWorkerRollingMixin):
    """Minimal mixin host: stubs every abstract method we depend on and
    records which stages were invoked during one tick."""

    def __init__(self, *, cfg: _Cfg) -> None:
        self._cfg = type("Cfg", (), {
            "instance_id": "bs1",
            "bluestacks_window_title": "BlueStacks 1",
        })()
        self._settings = type("Settings", (), {"worker": cfg})()
        self._stopping = False
        self._ui_paused = False
        self._task_busy = asyncio.Event()
        self._rolling_snap_seq = 0
        # Recorded stage invocations:
        self.calls: list[str] = []

    async def _run_blocking(self, fn: Any, /, *args: Any, **kwargs: Any) -> Any:
        # Synchronous shortcut — the actual worker uses a thread pool.
        return fn(*args, **kwargs)

    def _grab_layout_bgr(self) -> np.ndarray:
        self.calls.append("grab")
        return np.zeros((100, 100, 3), dtype=np.uint8)

    async def _detect_current_screen_on_frame(self, image_bgr: np.ndarray) -> str | None:
        self.calls.append("detect")
        return "main_city"

    async def _overlay_analyze_bgr(
        self, image_bgr: np.ndarray, *, current_screen_override: str | None = None
    ) -> None:
        self.calls.append("overlay")

    async def _maybe_enqueue_who_i_am_when_active_player_missing(self) -> None:
        self.calls.append("who_i_am")


@pytest.fixture
def _isolated_refs(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Any:
    """Redirect rolling-preview PNG writes to ``tmp_path`` so tests don't
    dirty the repo's real ``references/temporal/`` directory."""
    import worker.instance_worker_rolling as rolling_mod

    base_dir = tmp_path / "refs"
    base_dir.mkdir()

    def _fake_basename(_raw: str | None, _iid: str) -> str:
        return "rolling_preview"

    def _fake_abs_path(_root: Any, base: str, _iid: str) -> Any:
        return base_dir / f"{base}.png"

    monkeypatch.setattr(rolling_mod, "reference_file_basename", _fake_basename)
    monkeypatch.setattr(rolling_mod, "reference_png_abs_path", _fake_abs_path)
    return base_dir


@pytest.mark.asyncio
async def test_tick_idle_runs_full_pipeline(_isolated_refs: Any) -> None:
    h = _Harness(cfg=_Cfg())
    await h._device_reference_snapshot_tick()
    assert h.calls == ["grab", "detect", "overlay", "who_i_am"]


@pytest.mark.asyncio
async def test_tick_busy_skips_detect_and_overlay_by_default(_isolated_refs: Any) -> None:
    """Default config: both gates closed → after the screenshot the tick
    stops, no detect / overlay / who_i_am invocations."""
    h = _Harness(cfg=_Cfg())
    h._task_busy.set()
    await h._device_reference_snapshot_tick()
    assert h.calls == ["grab"], h.calls


@pytest.mark.asyncio
async def test_tick_busy_keeps_detect_when_flag_enabled(_isolated_refs: Any) -> None:
    """Operator can opt back in to background detect during busy by
    flipping ``screen_detect_when_busy=True``. Overlay still gated by
    its own flag."""
    h = _Harness(cfg=_Cfg(screen_detect_when_busy=True))
    h._task_busy.set()
    await h._device_reference_snapshot_tick()
    assert h.calls == ["grab", "detect"], h.calls


@pytest.mark.asyncio
async def test_tick_busy_keeps_full_pipeline_when_both_flags_enabled(
    _isolated_refs: Any,
) -> None:
    h = _Harness(
        cfg=_Cfg(screen_detect_when_busy=True, overlay_analyze_when_busy=True)
    )
    h._task_busy.set()
    await h._device_reference_snapshot_tick()
    assert h.calls == ["grab", "detect", "overlay", "who_i_am"], h.calls


@pytest.mark.asyncio
async def test_tick_busy_still_writes_preview_png(_isolated_refs: Any) -> None:
    """The PNG must still appear on disk during a busy tick — the UI
    watcher consumes it regardless of detect/overlay state."""
    h = _Harness(cfg=_Cfg())
    h._task_busy.set()
    await h._device_reference_snapshot_tick()
    written = list(_isolated_refs.iterdir())
    assert len(written) == 1
    assert written[0].suffix == ".png"
    assert written[0].stat().st_size > 0
