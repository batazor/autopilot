"""Tests for ``adb_reader.snooze_notification`` (on-device dismissal)."""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

from modules.notify import adb_reader


def _fake_run(captured, *, returncode=0, stderr=""):
    def run(cmd, capture_output=True, text=True, timeout=None):
        captured.append(cmd)
        return SimpleNamespace(returncode=returncode, stdout="", stderr=stderr)

    return run


def test_snooze_builds_quoted_device_command(monkeypatch):
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(captured))

    ok = adb_reader.snooze_notification(
        "0|com.gof.global|5|null|10080",
        duration_ms=604800000,
        adb_path="adb",
        serial="127.0.0.1:5555",
    )

    assert ok is True
    assert len(captured) == 1
    cmd = captured[0]
    # serial threaded through, single remote shell string with the key quoted so
    # the device shell doesn't parse the ``|`` separators as pipes.
    assert cmd[:3] == ["adb", "-s", "127.0.0.1:5555"]
    assert cmd[3] == "shell"
    assert cmd[4] == (
        "cmd notification snooze --for 604800000 '0|com.gof.global|5|null|10080'"
    )


def test_snooze_rejects_unsafe_key(monkeypatch):
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(captured))

    # A single quote would break out of the device-side quoting → rejected.
    assert adb_reader.snooze_notification("0|pkg|'; rm -rf /|1", duration_ms=1000) is False
    # Whitespace is rejected too.
    assert adb_reader.snooze_notification("0|pkg|a b|1", duration_ms=1000) is False
    # Empty key → nothing to do.
    assert adb_reader.snooze_notification("", duration_ms=1000) is False
    assert captured == [], "no adb call for an unsafe/empty key"


def test_snooze_rejects_nonpositive_duration(monkeypatch):
    captured: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(captured))

    assert adb_reader.snooze_notification("0|p|1|n|2", duration_ms=0) is False
    assert adb_reader.snooze_notification("0|p|1|n|2", duration_ms=-5) is False
    assert captured == []


def test_snooze_returns_false_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", _fake_run([], returncode=1, stderr="No such notification")
    )
    assert (
        adb_reader.snooze_notification("0|p|1|n|2", duration_ms=1000) is False
    )


def test_snooze_swallows_timeout(monkeypatch):
    def boom(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=15)

    monkeypatch.setattr(subprocess, "run", boom)
    # Best-effort: a hung adb must not raise out of the poll cycle.
    assert adb_reader.snooze_notification("0|p|1|n|2", duration_ms=1000) is False
