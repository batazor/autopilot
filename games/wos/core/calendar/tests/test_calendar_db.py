"""Tests for the per-state calendar schedule store (isolated temp state.db)."""
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


def _ev(name, d0, d1):
    return (name, datetime(2026, 6, d0, tzinfo=UTC), datetime(2026, 6, d1, tzinfo=UTC))


def test_replace_and_get_round_trip(_db):
    from games.wos.core.calendar import db

    n = db.replace_state_schedule("1234", [_ev("Mia's Fortune Hut", 12, 14), _ev("Hero Rally", 13, 16)])
    assert n == 2
    rows = db.get_state_schedule("1234")
    assert [r.name for r in rows] == ["Mia's Fortune Hut", "Hero Rally"]  # ordered by start
    assert rows[0].starts_at == datetime(2026, 6, 12, tzinfo=UTC).isoformat()


def test_replace_drops_rolled_off_events(_db):
    from games.wos.core.calendar import db

    db.replace_state_schedule("1234", [_ev("Old Event", 1, 2), _ev("Keep", 13, 16)])
    db.replace_state_schedule("1234", [_ev("Keep", 13, 16)])  # fresh read no longer has Old
    rows = db.get_state_schedule("1234")
    assert [r.name for r in rows] == ["Keep"]


def test_dedup_same_event_occurrence(_db):
    from games.wos.core.calendar import db

    # same event tapped twice across scroll frames → one row
    n = db.replace_state_schedule("1234", [_ev("Crazy Joe", 13, 14), _ev("Crazy Joe", 13, 14)])
    assert n == 1


def test_state_isolation(_db):
    from games.wos.core.calendar import db

    db.replace_state_schedule("1111", [_ev("A", 12, 13)])
    db.replace_state_schedule("2222", [_ev("B", 12, 13)])
    assert [r.name for r in db.get_state_schedule("1111")] == ["A"]
    assert [r.name for r in db.get_state_schedule("2222")] == ["B"]


def test_empty_clears_schedule(_db):
    from games.wos.core.calendar import db

    db.replace_state_schedule("1234", [_ev("A", 12, 13)])
    assert db.replace_state_schedule("1234", []) == 0
    assert db.get_state_schedule("1234") == []
