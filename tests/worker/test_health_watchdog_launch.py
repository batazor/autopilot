"""Watchdog launches via ``-c`` import, not ``-m`` (Nuitka ``.so`` compat).

Regression: ``python -m worker.game_health_watchdog`` crash-loops in the
compiled image because ``runpy`` can't ``get_code()`` a Nuitka extension
module. Launch by importing the module instead, and keep detection working for
both the new ``-c`` form and any legacy ``-m`` process still running.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import worker.health_watchdog_process as hw


def test_launch_uses_dash_c_import_not_dash_m(mocker) -> None:
    repo = Path("/app")
    mocker.patch.object(hw, "repo_root", return_value=repo)
    mocker.patch.object(hw, "existing_health_watchdog_process", return_value=None)
    fake_proc = types.SimpleNamespace(pid=1234, poll=lambda: None)
    popen = mocker.patch.object(hw.subprocess, "Popen", return_value=fake_proc)

    hw._health_proc = None
    hw._known_health_watchdog_pid = None
    hw.ensure_health_watchdog_process()

    argv = popen.call_args.args[0]
    assert argv[0] == sys.executable
    assert "-m" not in argv
    assert argv[1] == "-c"
    assert "worker.game_health_watchdog" in argv[2]
    assert "main()" in argv[2]


def _fake_psutil_proc(pid: int, cmdline: list[str], cwd: str):
    return types.SimpleNamespace(pid=pid, cmdline=lambda: cmdline, cwd=lambda: cwd)


def test_detects_new_dash_c_form(monkeypatch) -> None:
    monkeypatch.setattr(hw.os, "getpid", lambda: 1)
    proc = _fake_psutil_proc(
        99, [sys.executable, "-c", hw._HEALTH_WATCHDOG_LAUNCH_CODE], "/app"
    )
    assert hw.is_health_watchdog_process(proc, Path("/app")) is True


def test_detects_legacy_dash_m_form(monkeypatch) -> None:
    monkeypatch.setattr(hw.os, "getpid", lambda: 1)
    proc = _fake_psutil_proc(
        99, [sys.executable, "-m", "worker.game_health_watchdog"], "/app"
    )
    assert hw.is_health_watchdog_process(proc, Path("/app")) is True


def test_ignores_unrelated_process(monkeypatch) -> None:
    monkeypatch.setattr(hw.os, "getpid", lambda: 1)
    proc = _fake_psutil_proc(99, [sys.executable, "-m", "something.else"], "/app")
    assert hw.is_health_watchdog_process(proc, Path("/app")) is False
