"""Status-aware uptime in the fleet view."""
from __future__ import annotations

import time

import pytest

from api.services.fleet import _fleet_uptime


def _row(*, started_offset: float | None, last_seen_offset: float | None = None) -> dict[str, str]:
    now = time.time()
    row: dict[str, str] = {}
    if started_offset is not None:
        row["worker_started_at"] = str(now - started_offset)
    if last_seen_offset is not None:
        row["last_seen_at"] = str(now - last_seen_offset)
    return row


def test_live_returns_ticking_uptime() -> None:
    out = _fleet_uptime(_row(started_offset=125.0, last_seen_offset=2.0), "live")
    assert out != "—"
    assert ":" in out  # "2:05" or "0:02:05"


def test_paused_returns_ticking_uptime() -> None:
    out = _fleet_uptime(_row(started_offset=60.0, last_seen_offset=1.0), "paused")
    assert out != "—"


def test_stale_freezes_at_last_seen() -> None:
    # Worker started 1h ago, last heartbeat 30min ago -> frozen 30:00
    row = _row(started_offset=3600.0, last_seen_offset=1800.0)
    out = _fleet_uptime(row, "stale")
    assert out == "30:00"


def test_crashed_freezes_at_last_seen() -> None:
    row = _row(started_offset=125.0, last_seen_offset=5.0)
    out = _fleet_uptime(row, "crashed")
    # 120s = "2:00"
    assert out == "2:00"


def test_offline_blanks() -> None:
    assert _fleet_uptime(_row(started_offset=999.0), "offline") == "—"


def test_starting_blanks() -> None:
    assert _fleet_uptime(_row(started_offset=None), "starting") == "—"


def test_restarting_blanks_even_with_stamp() -> None:
    assert _fleet_uptime(_row(started_offset=300.0, last_seen_offset=10.0), "restarting") == "—"


def test_no_worker_started_at_blanks() -> None:
    assert _fleet_uptime({}, "live") == "—"


def test_malformed_timestamp_blanks() -> None:
    assert _fleet_uptime({"worker_started_at": "garbage"}, "live") == "—"


def test_stale_with_missing_last_seen_blanks() -> None:
    # Worker_started_at present but last_seen never written — can't compute
    # frozen uptime, so blank rather than show a misleading value.
    row = {"worker_started_at": str(time.time() - 100.0)}
    assert _fleet_uptime(row, "stale") == "—"


def test_stale_with_last_seen_before_start_blanks() -> None:
    # Clock skew / stale Redis data: refuse to show a negative interval.
    now = time.time()
    row = {
        "worker_started_at": str(now - 60.0),
        "last_seen_at": str(now - 120.0),
    }
    assert _fleet_uptime(row, "stale") == "—"


def test_long_uptime_formats_with_hours() -> None:
    out = _fleet_uptime(_row(started_offset=7325.0, last_seen_offset=2.0), "live")
    # 2:02:0X
    assert out.startswith("2:02:0")
