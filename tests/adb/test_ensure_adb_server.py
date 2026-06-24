"""Tests for adb.ensure_adb_server — start a local adb server, best-effort."""
from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from adb import screencap
from adb.screencap import ensure_adb_server

if TYPE_CHECKING:
    import pytest


class _Proc:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = stderr


def test_returns_false_when_adb_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screencap, "resolve_adb_executable", lambda _pref: None)
    calls: list[bool] = []
    monkeypatch.setattr(
        screencap.subprocess, "run", lambda *_a, **_k: calls.append(True) or _Proc()
    )
    assert ensure_adb_server("adb") is False
    assert calls == []  # never shells out without a binary


def test_runs_start_server_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screencap, "resolve_adb_executable", lambda _pref: "/usr/bin/adb")
    calls: list[list[str]] = []

    def _run(cmd: list[str], **_k: object) -> _Proc:
        calls.append(cmd)
        return _Proc(returncode=0)

    monkeypatch.setattr(screencap.subprocess, "run", _run)
    assert ensure_adb_server("adb") is True
    assert calls == [["/usr/bin/adb", "start-server"]]


def test_returns_false_on_nonzero_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screencap, "resolve_adb_executable", lambda _pref: "/usr/bin/adb")
    monkeypatch.setattr(
        screencap.subprocess, "run", lambda *_a, **_k: _Proc(returncode=1, stderr="boom")
    )
    assert ensure_adb_server("adb") is False


def test_returns_false_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screencap, "resolve_adb_executable", lambda _pref: "/usr/bin/adb")

    def _boom(*_a: object, **_k: object) -> _Proc:
        msg = "no exec"
        raise OSError(msg)

    monkeypatch.setattr(screencap.subprocess, "run", _boom)
    assert ensure_adb_server("adb") is False


def test_returns_false_on_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(screencap, "resolve_adb_executable", lambda _pref: "/usr/bin/adb")

    def _slow(*_a: object, **_k: object) -> _Proc:
        raise subprocess.TimeoutExpired(cmd="adb", timeout=1.0)

    monkeypatch.setattr(screencap.subprocess, "run", _slow)
    assert ensure_adb_server("adb") is False
