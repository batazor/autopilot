"""Tests for src/config/devices_db.py — SQLite store for the device registry."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.device_display import DeviceDisplayConfig
from config.devices_db import (
    count_devices,
    delete_device,
    device_exists,
    load_registry,
    set_device_backend,
    set_device_game,
    set_profile_game,
    upsert_device,
    upsert_device_gamer,
)
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


# ---------------------------------------------------------------------------
# upsert_device + load_registry
# ---------------------------------------------------------------------------


def test_empty_registry(sqlite_db: Path) -> None:
    assert count_devices() == 0
    registry = load_registry()
    assert registry.devices == []


def test_upsert_then_load(sqlite_db: Path) -> None:
    upsert_device("bs1", adb_serial="127.0.0.1:5555")
    registry = load_registry()
    assert len(registry.devices) == 1
    assert registry.devices[0].name == "bs1"
    assert registry.devices[0].adb_serial == "127.0.0.1:5555"
    assert registry.devices[0].profiles == ()


def test_upsert_replaces_existing(sqlite_db: Path) -> None:
    upsert_device("bs1", adb_serial="OLD")
    upsert_device("bs1", adb_serial="NEW", screenshot_backend="scrcpy")
    assert count_devices() == 1
    device = load_registry().devices[0]
    assert device.adb_serial == "NEW"
    assert device.screenshot_backend == "scrcpy"


def test_upsert_persists_display_and_quartz(sqlite_db: Path) -> None:
    display = DeviceDisplayConfig(size="720x1280", density=320, brightness_percent=70)
    upsert_device(
        "bs1",
        adb_serial="127.0.0.1:5555",
        quartz_window_id=42,
        quartz_window_title="BlueStacks",
        quartz_crop=(10, 20, 700, 1200),
        display=display,
    )
    device = load_registry().devices[0]
    assert device.quartz_window_id == 42
    assert device.quartz_window_title == "BlueStacks"
    assert device.quartz_crop == (10, 20, 700, 1200)
    assert device.display == display


def test_upsert_with_empty_name_raises(sqlite_db: Path) -> None:
    with pytest.raises(ValueError, match="device name is required"):
        upsert_device("   ")


# ---------------------------------------------------------------------------
# profile + gamer mutation
# ---------------------------------------------------------------------------


def test_upsert_gamer_creates_device_profile_and_gamer(sqlite_db: Path) -> None:
    changed = upsert_device_gamer("bs1", "401227964", "batazor")
    assert changed is True
    registry = load_registry()
    assert len(registry.devices) == 1
    device = registry.devices[0]
    assert len(device.profiles) == 1
    assert device.profiles[0].email == ""
    assert len(device.profiles[0].gamers) == 1
    assert device.profiles[0].gamers[0].id == 401227964
    assert device.profiles[0].gamers[0].nickname == "batazor"


def test_upsert_gamer_appends_to_existing_profile(sqlite_db: Path) -> None:
    upsert_device_gamer("bs1", "1", "first")
    upsert_device_gamer("bs1", "2", "second")
    upsert_device_gamer("bs1", "3", "third")

    gamers = load_registry().devices[0].profiles[0].gamers
    assert [g.id for g in gamers] == [1, 2, 3], "insertion order must be preserved"


def test_upsert_gamer_matches_by_adb_serial(sqlite_db: Path) -> None:
    upsert_device("bs1", adb_serial="127.0.0.1:5555")
    changed = upsert_device_gamer("127.0.0.1:5555", "42", "hero")
    assert changed is True
    # No duplicate device row created — still just "bs1".
    assert count_devices() == 1
    gamers = load_registry().devices[0].profiles[0].gamers
    assert gamers[0].id == 42


def test_upsert_gamer_same_data_returns_false(sqlite_db: Path) -> None:
    assert upsert_device_gamer("bs1", "1", "hero") is True
    assert upsert_device_gamer("bs1", "1", "hero") is False


def test_upsert_gamer_updates_nickname(sqlite_db: Path) -> None:
    upsert_device_gamer("bs1", "1", "old_nick")
    assert upsert_device_gamer("bs1", "1", "new_nick") is True
    assert load_registry().devices[0].profiles[0].gamers[0].nickname == "new_nick"


def test_upsert_gamer_rejects_empty_inputs(sqlite_db: Path) -> None:
    assert upsert_device_gamer("", "1", "x") is False
    assert upsert_device_gamer("bs1", "", "x") is False
    assert upsert_device_gamer("bs1", "not-int", "x") is False


# ---------------------------------------------------------------------------
# set_device_backend
# ---------------------------------------------------------------------------


def test_set_backend_partial_update(sqlite_db: Path) -> None:
    upsert_device(
        "bs1",
        adb_serial="X",
        screenshot_backend="quartz",
        input_backend="adb",
    )
    new_screenshot, new_input = set_device_backend("bs1", screenshot_backend="scrcpy")
    assert new_screenshot == "scrcpy"
    assert new_input == "adb"  # left intact


def test_set_backend_clears_with_empty_string(sqlite_db: Path) -> None:
    upsert_device("bs1", adb_serial="X", screenshot_backend="scrcpy")
    new_screenshot, _ = set_device_backend("bs1", screenshot_backend="")
    assert new_screenshot == ""


def test_set_backend_rejects_unknown_value(sqlite_db: Path) -> None:
    upsert_device("bs1", adb_serial="X")
    with pytest.raises(ValueError, match="screenshot_backend"):
        set_device_backend("bs1", screenshot_backend="nonsense")
    with pytest.raises(ValueError, match="screenshot_backend"):
        set_device_backend("bs1", screenshot_backend="minicap")
    with pytest.raises(ValueError, match="input_backend"):
        set_device_backend("bs1", input_backend="hyperdrive")


def test_set_backend_unknown_device_raises(sqlite_db: Path) -> None:
    with pytest.raises(KeyError):
        set_device_backend("does-not-exist", screenshot_backend="adb")


# ---------------------------------------------------------------------------
# delete_device cascades
# ---------------------------------------------------------------------------


def test_delete_device_cascades_profiles_and_gamers(sqlite_db: Path) -> None:
    upsert_device_gamer("bs1", "1", "a")
    upsert_device_gamer("bs1", "2", "b")
    assert delete_device("bs1") is True
    assert not device_exists("bs1")
    assert load_registry().devices == []


def test_delete_unknown_device_returns_false(sqlite_db: Path) -> None:
    assert delete_device("ghost") is False


# ---------------------------------------------------------------------------
# game column (Phase 2)
# ---------------------------------------------------------------------------


def test_new_device_defaults_to_wos(sqlite_db: Path) -> None:
    upsert_device("bs1")
    entry = load_registry().devices[0]
    assert entry.game == "wos"


def test_upsert_device_accepts_explicit_game(sqlite_db: Path) -> None:
    upsert_device("bs1", game="kingshot")
    assert load_registry().devices[0].game == "kingshot"


def test_upsert_device_preserves_game_when_omitted(sqlite_db: Path) -> None:
    upsert_device("bs1", game="kingshot")
    upsert_device("bs1", adb_serial="127.0.0.1:5555")  # no game kwarg
    entry = load_registry().devices[0]
    assert entry.game == "kingshot"
    assert entry.adb_serial == "127.0.0.1:5555"


def test_upsert_device_rejects_unknown_game(sqlite_db: Path) -> None:
    with pytest.raises(ValueError, match="unknown game id"):
        upsert_device("bs1", game="brawl_stars")


def test_set_device_game_updates_existing(sqlite_db: Path) -> None:
    upsert_device("bs1")
    assert set_device_game("bs1", "kingshot") == "kingshot"
    assert load_registry().devices[0].game == "kingshot"


def test_set_device_game_rejects_unknown_game(sqlite_db: Path) -> None:
    upsert_device("bs1")
    with pytest.raises(ValueError, match="unknown game id"):
        set_device_game("bs1", "brawl_stars")


def test_set_device_game_missing_device(sqlite_db: Path) -> None:
    with pytest.raises(KeyError, match="device not found"):
        set_device_game("ghost", "wos")


def test_profile_inherits_device_game_by_default(sqlite_db: Path) -> None:
    upsert_device("bs1", game="kingshot")
    upsert_device_gamer("bs1", "111", "alice")
    profile = load_registry().devices[0].profiles[0]
    assert profile.game == "kingshot"


def test_set_profile_game_overrides_device_default(sqlite_db: Path) -> None:
    upsert_device("bs1", game="wos")
    upsert_device_gamer("bs1", "111", "alice")
    # Profile id of the first profile under bs1
    import sqlite3
    conn = sqlite3.connect(str(sqlite_db))
    profile_id = conn.execute(
        "SELECT id FROM device_profiles WHERE device_name = ? ORDER BY id LIMIT 1", ("bs1",)
    ).fetchone()[0]
    conn.close()

    assert set_profile_game(profile_id, "kingshot") == "kingshot"
    profile = load_registry().devices[0].profiles[0]
    assert profile.game == "kingshot"
    # Device default unchanged
    assert load_registry().devices[0].game == "wos"
