"""Durable SQLite task-history mirror: write/read roundtrip, retention purge,
and the fetch_queue_history_rows Redis→SQLite top-up fallback."""
from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest

import config.task_history_db as thdb
from config.state_sqlite import set_state_db_path_for_tests
from config.task_history_db import (
    RETENTION_SECONDS,
    fetch_task_history_dicts,
    record_task_history,
)

if TYPE_CHECKING:
    from pathlib import Path


def _row(task_id: str, *, instance_id: str = "bs1", finished_at: float | None = None,
         success: bool = True) -> dict[str, Any]:
    fin = time.time() if finished_at is None else finished_at
    return {
        "task_id": task_id,
        "task_type": "dsl_scenario",
        "scenario": "check_main_city",
        "player_id": "401227964",
        "instance_id": instance_id,
        "priority": 80_000,
        "region": "",
        "started_at": fin - 12.5,
        "finished_at": fin,
        "duration_s": 12.5,
        "success": success,
        "error": "",
        "reason": "" if success else "match_region_not_found",
        "metadata": {"steps_total": 3, "scenario_completed": success},
        "trace_id": "a" * 32,
        "span_id": "b" * 16,
    }


@pytest.fixture
def history_db(tmp_path: Path):
    set_state_db_path_for_tests(tmp_path / "state.db")
    thdb._next_purge_ts = 0.0
    yield
    set_state_db_path_for_tests(None)
    thdb._next_purge_ts = 0.0


def test_record_fetch_roundtrip(history_db: None) -> None:
    row = _row("t1")
    record_task_history(row)
    got = fetch_task_history_dicts("bs1", limit=10)
    assert len(got) == 1
    assert got[0] == json.loads(json.dumps(row))  # shape survives serialization


def test_fetch_is_newest_first_and_instance_scoped(history_db: None) -> None:
    now = time.time()
    record_task_history(_row("old", finished_at=now - 100))
    record_task_history(_row("new", finished_at=now))
    record_task_history(_row("other", instance_id="bs2", finished_at=now))
    got = fetch_task_history_dicts("bs1", limit=10)
    assert [d["task_id"] for d in got] == ["new", "old"]


def test_purge_drops_rows_past_retention(history_db: None) -> None:
    now = time.time()
    record_task_history(_row("ancient", finished_at=now - RETENTION_SECONDS - 60))
    record_task_history(_row("fresh", finished_at=now))
    thdb._next_purge_ts = 0.0
    thdb._maybe_purge(now=now)
    got = fetch_task_history_dicts("bs1", limit=10)
    assert [d["task_id"] for d in got] == ["fresh"]


class _StubRedis:
    """Minimal lrange-only stand-in for the history read path."""

    def __init__(self, payloads: list[str]) -> None:
        self._payloads = payloads

    def lrange(self, key: str, start: int, stop: int) -> list[str]:
        return self._payloads[start : stop + 1]


def test_fetch_queue_history_rows_falls_back_to_sqlite(history_db: None) -> None:
    from dashboard.redis_client import fetch_queue_history_rows

    now = time.time()
    record_task_history(_row("mirror-1", finished_at=now - 10))
    record_task_history(_row("mirror-2", finished_at=now - 20))

    rows = fetch_queue_history_rows(_StubRedis([]), instance_id="bs1", limit=5)  # type: ignore[arg-type]
    assert [r.task_id for r in rows] == ["mirror-1", "mirror-2"]
    assert rows[0].scenario == "check_main_city"
    assert rows[0].steps_total == 3


def test_fetch_queue_history_rows_tops_up_and_dedups(history_db: None) -> None:
    from dashboard.redis_client import fetch_queue_history_rows

    now = time.time()
    # "live" is only in Redis; "shared" is mirrored in both; "older" only in SQLite.
    record_task_history(_row("shared", finished_at=now - 5))
    record_task_history(_row("older", finished_at=now - 50))
    redis_payloads = [
        json.dumps(_row("live", finished_at=now)),
        json.dumps(_row("shared", finished_at=now - 5)),
    ]

    rows = fetch_queue_history_rows(
        _StubRedis(redis_payloads), instance_id="bs1", limit=5  # type: ignore[arg-type]
    )
    assert [r.task_id for r in rows] == ["live", "shared", "older"]
