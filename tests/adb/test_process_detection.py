"""Tests for the resilient multi-method game-process detector.

Covers the fallback chain, BlueStacks-first ordering, partial sub-process
matching, and the "process dead" vs "detection failed" distinction.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from adb.controller import AdbController, ProcessDetection, _ShellOutcome

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest

PKG = "com.gof.global"  # the WOS package
BETA_PKG = "com.xyz.gof"  # WOS beta package

# Realistic `ps -A` dump: main process, a sub-process, a look-alike that must
# NOT match (``com.gof.globalX``), plus unrelated system processes.
PS_OUT = """\
USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME
u0_a1         1234   555 1000000  50000 0                   0 S com.gof.global
u0_a1         1240   555  900000  40000 0                   0 S com.gof.global:render
u0_a1         1250   555  900000  40000 0                   0 S com.gof.globalX
root           999     1   10000   2000 0                   0 S zygote"""

BETA_PS_OUT = """\
USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME
u0_a2         2234   555 1000000  50000 0                   0 S com.xyz.gof
u0_a2         2240   555  900000  40000 0                   0 S com.xyz.gof:render
u0_a2         2250   555  900000  40000 0                   0 S com.xyz.gofX
root           999     1   10000   2000 0                   0 S zygote"""

EMPTY_PS = "USER           PID  PPID     VSZ    RSS WCHAN            ADDR S NAME"


def _mk(rc: int | None, out: str = "", err: str = "") -> _ShellOutcome:
    return _ShellOutcome(rc=rc, stdout=out, stderr=err)


def _controller(
    serial: str,
    shell: Callable[..., _ShellOutcome],
    shell_text: Callable[..., str] | None = None,
) -> AdbController:
    """Build a controller without touching ADB, wiring a fake ``_shell_full``."""
    ctrl = AdbController.__new__(AdbController)
    ctrl._serial = serial
    ctrl._adb_exe = "adb"
    ctrl._shell_full = shell  # type: ignore[method-assign]
    if shell_text is not None:
        ctrl._shell = shell_text  # type: ignore[method-assign]
    return ctrl


def test_ps_returns_main_and_sub_process_pids() -> None:
    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        if args[:2] == ("ps", "-A"):
            return _mk(0, PS_OUT)
        msg = f"ps should have answered first: {args}"
        raise AssertionError(msg)

    # 127.0.0.1 serial => BlueStacks => ps leads.
    res = _controller("127.0.0.1:5555", shell).detect_game_process("wos")

    assert res == ProcessDetection(
        found=True, pids=[1234, 1240], method_used="ps", error=None
    )
    # Look-alike package must not leak in.
    assert 1250 not in res.pids


def test_falls_through_ps_to_dumpsys_when_no_pids() -> None:
    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        if args[:2] == ("ps", "-A"):
            return _mk(0, EMPTY_PS)  # clean, but game not in ps
        if args[:3] == ("dumpsys", "activity", "recents"):
            return _mk(0, "Recent #0: TaskRecord{... A=com.gof.global U=0 ...}")
        msg = f"unexpected call {args}"
        raise AssertionError(msg)

    res = _controller("127.0.0.1:5555", shell).detect_game_process("wos")

    assert res.found is True
    assert res.method_used == "dumpsys_recents"
    assert res.pids == []  # presence-only method yields no PIDs
    assert res.error is None


def test_legacy_ps_dash_a_rejected_retries_bare_ps() -> None:
    calls: list[tuple[str, ...]] = []

    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        calls.append(args)
        if args == ("ps", "-A"):
            return _mk(1, "", "bad -A")  # toybox rejects -A
        if args == ("ps",):
            return _mk(0, PS_OUT)
        msg = f"unexpected call {args}"
        raise AssertionError(msg)

    res = _controller("127.0.0.1:5555", shell).detect_game_process("wos")

    assert res.found is True
    assert res.pids == [1234, 1240]
    assert ("ps", "-A") in calls and ("ps",) in calls


def test_all_methods_failing_sets_error_not_dead() -> None:
    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        return _mk(None)  # everything times out

    res = _controller("127.0.0.1:5555", shell).detect_game_process("wos")

    assert res.found is False
    assert res.method_used == "none"
    assert res.error is not None  # "we could not ask" — not a clean miss
    # is_game_running collapses this to False (watchdog retries absorb it).
    assert _controller("127.0.0.1:5555", shell).is_game_running("wos") is False


def test_clean_misses_everywhere_report_dead_without_error() -> None:
    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        if args[0] == "ps":
            return _mk(0, EMPTY_PS)
        if args[0] in ("dumpsys", "am"):
            return _mk(0, "")  # ran fine, package absent
        if args[0] == "pidof":
            return _mk(1, "")  # silent rc=1 => clean "not found"
        msg = f"unexpected call {args}"
        raise AssertionError(msg)

    res = _controller("127.0.0.1:5555", shell).detect_game_process("wos")

    assert res.found is False
    assert res.error is None  # genuinely dead, distinguishable from failure


def test_pidof_rc1_with_stderr_is_failure_not_miss() -> None:
    # On a real device pidof leads; a noisy rc=1 means the call broke, so the
    # detector must fall through to ps rather than declaring the game dead.
    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        if args[0] == "pidof":
            return _mk(1, "", "pidof: permission denied")
        if args[:2] == ("ps", "-A"):
            return _mk(0, PS_OUT)
        msg = f"unexpected call {args}"
        raise AssertionError(msg)

    # Non-127.0.0.1, non-emulator serial => real device => pidof first.
    res = _controller("RF8RC00M8MF", shell).detect_game_process("wos")

    assert res.found is True
    assert res.method_used == "ps"  # fell through past the broken pidof


def test_real_device_tries_pidof_first() -> None:
    order: list[str] = []

    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        order.append(args[0])
        if args[0] == "pidof":
            return _mk(0, "4321")
        return _mk(0, "")

    res = _controller("RF8RC00M8MF", shell).detect_game_process("wos")

    assert order[0] == "pidof"
    assert res.found is True
    assert res.pids == [4321]
    assert res.method_used == "pidof"


def test_detects_wos_beta_package_alias() -> None:
    def shell(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        if args[:2] == ("ps", "-A"):
            return _mk(0, BETA_PS_OUT)
        if args[:3] == ("dumpsys", "activity", "recents"):
            return _mk(0, "")
        if args[:3] == ("am", "stack", "list"):
            return _mk(0, "")
        if args[0] == "pidof":
            return _mk(1, "")
        msg = f"unexpected call {args}"
        raise AssertionError(msg)

    res = _controller("127.0.0.1:5625", shell).detect_game_process("wos")

    assert res == ProcessDetection(
        found=True, pids=[2234, 2240], method_used="ps", error=None
    )
    assert 2250 not in res.pids


def test_list_installed_games_treats_wos_beta_as_wos() -> None:
    def shell_full(*_args: str, timeout: float = 15.0) -> _ShellOutcome:
        return _mk(0, "")

    def shell_text(*args: str, timeout: float = 15.0) -> str:
        assert args == ("pm", "list", "packages")
        return "package:com.xyz.gof\npackage:com.android.systemui"

    assert _controller(
        "127.0.0.1:5625",
        shell_full,
        shell_text,
    ).list_installed_games() == ["wos"]


def test_ensure_game_foreground_launches_running_wos_beta_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []

    def shell_full(*args: str, timeout: float = 15.0) -> _ShellOutcome:
        if args[:2] == ("ps", "-A"):
            return _mk(0, BETA_PS_OUT)
        if args[:3] == ("dumpsys", "activity", "recents"):
            return _mk(0, "")
        if args[:3] == ("am", "stack", "list"):
            return _mk(0, "")
        if args[0] == "pidof":
            return _mk(1, "")
        msg = f"unexpected call {args}"
        raise AssertionError(msg)

    def shell_text(*args: str, timeout: float = 15.0) -> str:
        calls.append(args)
        if args[:3] == ("dumpsys", "activity", "activities"):
            return "ResumedActivity: com.uncube.launcher3/.HomeActivity"
        return ""

    monkeypatch.setattr("adb.controller.time.sleep", lambda _seconds: None)

    _controller(
        "127.0.0.1:5625",
        shell_full,
        shell_text,
    ).ensure_game_foreground("wos")

    assert (
        "monkey",
        "-p",
        BETA_PKG,
        "-c",
        "android.intent.category.LAUNCHER",
        "1",
    ) in calls
