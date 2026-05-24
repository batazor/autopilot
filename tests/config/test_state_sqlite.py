from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.state_schema import GamerState, StateDB
from config.state_sqlite import (
    get_player_stats,
    load_state_db_raw,
    record_player_stats,
    save_state_db,
    set_state_db_path_for_tests,
)

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_state(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "wos.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


def test_save_load_roundtrip(sqlite_state: Path) -> None:
    g = GamerState(id=42, nickname="Alice", power=9000)
    g.buildings.furnace.level = 5
    save_state_db(StateDB(gamers=[g]))
    db, err, _ = load_state_db_raw()
    assert err is None
    assert db is not None
    assert len(db.gamers) == 1
    assert db.gamers[0].nickname == "Alice"
    assert db.gamers[0].power == 9000


def test_daily_power_and_level_event(sqlite_state: Path) -> None:
    g = GamerState(id=1, power=100)
    g.buildings.furnace.level = 3
    save_state_db(StateDB(gamers=[g]))
    record_player_stats(g)

    g2 = g.model_copy(deep=True)
    g2.power = 150
    g2.buildings.furnace.level = 4
    record_player_stats(g2)

    stats = get_player_stats("1")
    assert len(stats["series"]) == 1
    assert stats["series"][0]["power"] == 150
    assert stats["series"][0]["furnace_level"] == 4
    assert len(stats["level_events"]) == 2
    assert stats["level_events"][-1]["level"] == 4


