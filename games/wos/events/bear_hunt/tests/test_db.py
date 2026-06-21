"""Tests for the per-alliance Bear Hunt trap store (isolated temp state.db)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from config import orm
from config.state_sqlite import set_state_db_path_for_tests


@pytest.fixture
def _db(tmp_path):
    set_state_db_path_for_tests(tmp_path / "state.db")
    orm.reset_for_tests()
    yield
    set_state_db_path_for_tests(None)
    orm.reset_for_tests()


def _dt(day, hour):
    return datetime(2026, 6, day, hour, tzinfo=UTC)


def test_upsert_and_get_round_trip(_db):
    from games.wos.events.bear_hunt import db

    n = db.upsert_traps("Wolves", {"1": (_dt(15, 6), 5), "2": (_dt(16, 19), 4)})
    assert n == 2
    rows = db.get_traps("Wolves")
    assert [r.trap_id for r in rows] == ["1", "2"]  # ordered by trap_id
    assert rows[0].ready_at == _dt(15, 6).isoformat()
    assert rows[0].level == 5
    assert rows[1].level == 4
    assert rows[1].window_minutes == 30


def test_upsert_overwrites_same_trap(_db):
    from games.wos.events.bear_hunt import db

    db.upsert_traps("Wolves", {"1": (_dt(15, 6), 4)})
    db.upsert_traps("Wolves", {"1": (_dt(17, 8), 5)})  # re-read, later ready + level
    rows = db.get_traps("Wolves")
    assert len(rows) == 1
    assert rows[0].ready_at == _dt(17, 8).isoformat()
    assert rows[0].level == 5


def test_alliance_isolation(_db):
    from games.wos.events.bear_hunt import db

    db.upsert_traps("Wolves", {"1": (_dt(15, 6), 3)})
    db.upsert_traps("Bears", {"1": (_dt(16, 6), 2)})
    assert [r.ready_at for r in db.get_traps("Wolves")] == [_dt(15, 6).isoformat()]
    assert [r.ready_at for r in db.get_traps("Bears")] == [_dt(16, 6).isoformat()]


def test_empty_inputs_are_noops(_db):
    from games.wos.events.bear_hunt import db

    assert db.upsert_traps("", {"1": (_dt(15, 6), 5)}) == 0
    assert db.upsert_traps("Wolves", {}) == 0
    assert db.get_traps("") == []
