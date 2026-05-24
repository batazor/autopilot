"""upsert_device_gamer — semantics over the SQLite store."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from config import devices as devices_mod
from config.devices import load_devices, upsert_device_gamer
from config.devices_db import upsert_device
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Any:
    db_path = tmp_path / "db" / "state" / "wos.db"
    set_state_db_path_for_tests(db_path)
    devices_mod._invalidate()
    yield db_path
    devices_mod._invalidate()
    set_state_db_path_for_tests(None)


def test_upsert_device_gamer_matches_by_adb_serial(sqlite_db: Path) -> None:
    """When the registry was seeded with ``adb_serial`` only, calling upsert
    with the serial as ``device_name`` must hit the existing entry rather than
    creating a duplicate row keyed by the serial."""
    upsert_device("bs1", adb_serial="127.0.0.1:5555")

    ok = upsert_device_gamer(
        device_name="127.0.0.1:5555",
        player_id="765502864",
        nickname="lord",
    )
    assert ok is True

    registry = load_devices()
    assert len(registry.devices) == 1
    gamers = registry.devices[0].profiles[0].gamers
    assert len(gamers) == 1
    assert gamers[0].id == 765502864
    assert gamers[0].nickname == "lord"


def test_upsert_device_gamer_appends_new_device_when_no_match(sqlite_db: Path) -> None:
    ok = upsert_device_gamer(
        device_name="bs2",
        player_id="42",
        nickname="newcomer",
    )
    assert ok is True

    registry = load_devices()
    assert registry.devices[0].name == "bs2"
    assert registry.devices[0].profiles[0].gamers[0].id == 42
    assert registry.devices[0].profiles[0].gamers[0].nickname == "newcomer"


def test_upsert_device_gamer_noop_returns_false_when_nothing_changed(sqlite_db: Path) -> None:
    """A repeat call with the same nickname is a no-op — must return False so
    callers don't trigger a spurious 'linked' toast."""
    assert upsert_device_gamer(
        device_name="bs1",
        player_id="1",
        nickname="hero",
    ) is True
    assert upsert_device_gamer(
        device_name="bs1",
        player_id="1",
        nickname="hero",
    ) is False


def test_upsert_device_gamer_updates_nickname(sqlite_db: Path) -> None:
    """When the same player gets a new nickname, the row is updated in place
    (no second gamer row, returns True for the change)."""
    upsert_device_gamer(device_name="bs1", player_id="1", nickname="old")
    assert upsert_device_gamer(device_name="bs1", player_id="1", nickname="new") is True

    registry = load_devices()
    gamers = registry.devices[0].profiles[0].gamers
    assert len(gamers) == 1
    assert gamers[0].nickname == "new"
