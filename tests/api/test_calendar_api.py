"""Tests for the calendar schedule API service (isolated temp state.db)."""
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


def test_build_calendar_view_lists_states_and_events(_db):
    from games.wos.core.calendar import db

    from api.services import calendar_api

    db.replace_state_schedule("1111", [_ev("Foundry Battle", 12, 14)])
    db.replace_state_schedule("2222", [_ev("Hero Rally", 13, 16)])

    view = calendar_api.build_calendar_view(game="wos", days=7)
    assert [s["state"] for s in view["states"]] == ["1111", "2222"]
    s0 = view["states"][0]
    assert s0["event_count"] == 1
    assert s0["events"][0]["name"] == "Foundry Battle"
    assert s0["events"][0]["state_flag"] == "event_foundry_battle"
    assert s0["updated_at"] is not None


def test_build_calendar_view_empty(_db):
    from api.services import calendar_api

    view = calendar_api.build_calendar_view()
    assert view["states"] == []
