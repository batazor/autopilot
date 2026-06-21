"""Backend dispatch tests — devices now persist in SQLite, not YAML."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from config.devices import load_devices
from config.devices_db import upsert_device
from config.loader import load_settings
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def sqlite_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    yield db_path
    set_state_db_path_for_tests(None)


def test_devices_default_to_empty_screenshot_backend(sqlite_db: Path) -> None:
    """Empty marker = smart default (scrcpy for every device), chosen later by
    the dispatcher in bot_actions."""
    upsert_device("bs1", adb_serial="127.0.0.1:5555")

    registry = load_devices()
    device = registry.devices[0]
    assert device.screenshot_backend == ""


def test_devices_parse_scrcpy_screenshot_backend(sqlite_db: Path) -> None:
    upsert_device("phone", adb_serial="RF8RC00M8MF", screenshot_backend="scrcpy")

    registry = load_devices()
    assert registry.devices[0].screenshot_backend == "scrcpy"


def test_devices_persist_explicit_screenshot_backend(sqlite_db: Path) -> None:
    upsert_device(
        "bs1",
        adb_serial="127.0.0.1:5555",
        screenshot_backend="adb",
    )

    registry = load_devices()
    device = registry.devices[0]
    assert device.screenshot_backend == "adb"


def test_settings_instances_include_screenshot_backend(
    sqlite_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import config.paths

    monkeypatch.setattr(config.paths, "repo_root", lambda: tmp_path)
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("redis:\n  url: redis://localhost:6379/0\n", encoding="utf-8")
    upsert_device("bs1", adb_serial="127.0.0.1:5555", screenshot_backend="adb")

    settings = load_settings(settings_path)
    assert settings.instances[0].screenshot_backend == "adb"


def test_load_devices_empty_when_sqlite_blank(sqlite_db: Path) -> None:
    registry = load_devices()
    assert registry.devices == []
