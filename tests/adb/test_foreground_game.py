"""Tests for foreground-game detection used to adopt the game actually running.

``current_foreground_game`` reverse-looks-up the resumed activity's package
against the game registry; ``detect_running_game`` prefers that and falls back
to a live-process scan. These drive the worker's "play the game that's on
screen, not the configured one" startup behavior.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from adb.controller import AdbController, _ShellOutcome

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

WOS_PKG = "com.gof.global"
KINGSHOT_PKG = "com.run.tower.defense"


def _activities_dump(component: str) -> str:
    return (
        "  ResumedActivity: ActivityRecord{abc u0 "
        f"{component} t42}}\n"
        "  mResumedActivity: ActivityRecord{abc u0 "
        f"{component} t42}}\n"
    )


def _controller(
    serial: str,
    shell_text: Callable[..., str] | None = None,
    shell_full: Callable[..., _ShellOutcome] | None = None,
) -> AdbController:
    ctrl = AdbController.__new__(AdbController)
    ctrl._instance_id = "bs1"
    ctrl._serial = serial
    ctrl._adb_exe = "adb"
    if shell_text is not None:
        ctrl._shell = shell_text  # type: ignore[method-assign]
    if shell_full is not None:
        ctrl._shell_full = shell_full  # type: ignore[method-assign]
    ctrl._refresh_rolling_preview = lambda: None  # type: ignore[method-assign]
    return ctrl


def test_current_foreground_game_maps_resumed_activity_to_wos() -> None:
    def shell(*args: str, timeout: float = 10.0) -> str:
        assert args[:3] == ("dumpsys", "activity", "activities")
        return _activities_dump(f"{WOS_PKG}/com.gof.MainActivity")

    assert _controller("127.0.0.1:5555", shell).current_foreground_game() == "wos"


def test_current_foreground_game_maps_kingshot() -> None:
    def shell(*args: str, timeout: float = 10.0) -> str:
        return _activities_dump(f"{KINGSHOT_PKG}/com.run.MainActivity")

    assert _controller("127.0.0.1:5555", shell).current_foreground_game() == "kingshot"


def test_current_foreground_game_returns_none_for_launcher() -> None:
    def shell(*args: str, timeout: float = 10.0) -> str:
        return _activities_dump("com.bluestacks.launcher/.HomeActivity")

    assert _controller("127.0.0.1:5555", shell).current_foreground_game() is None


def test_detect_running_game_prefers_foreground(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def shell(*args: str, timeout: float = 10.0) -> str:
        return _activities_dump(f"{WOS_PKG}/com.gof.MainActivity")

    ctrl = _controller("127.0.0.1:5555", shell)

    # Foreground hit must short-circuit before any process scan.
    def _no_scan(*_a: object, **_k: object) -> str | None:
        msg = "should not scan"
        raise AssertionError(msg)

    monkeypatch.setattr(ctrl, "_running_package_for_game", _no_scan)
    assert ctrl.detect_running_game() == "wos"


def test_detect_running_game_falls_back_to_live_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Launcher foreground (no known game), but kingshot has a live process.
    def shell(*args: str, timeout: float = 10.0) -> str:
        return _activities_dump("com.bluestacks.launcher/.HomeActivity")

    ctrl = _controller("127.0.0.1:5555", shell)
    monkeypatch.setattr(
        ctrl,
        "_running_package_for_game",
        lambda game_id, *_a, **_k: KINGSHOT_PKG if game_id == "kingshot" else None,
    )
    assert ctrl.detect_running_game() == "kingshot"


def test_detect_running_game_none_when_nothing_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def shell(*args: str, timeout: float = 10.0) -> str:
        return _activities_dump("com.bluestacks.launcher/.HomeActivity")

    ctrl = _controller("127.0.0.1:5555", shell)
    monkeypatch.setattr(ctrl, "_running_package_for_game", lambda *_a, **_k: None)
    assert ctrl.detect_running_game() is None
