"""Tests for set_device_backend — SQLite-backed devices store."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException

from api.services import adb_api
from config.devices_db import load_registry, upsert_device
from config.state_sqlite import set_state_db_path_for_tests

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def devices_db(tmp_path: Path) -> Path:
    """Fresh SQLite per test, seeded with two devices: bs1 (no backends) and phone2 (both set)."""
    db_path = tmp_path / "db" / "state" / "state.db"
    set_state_db_path_for_tests(db_path)
    upsert_device("bs1", adb_serial="RF8RC00M8MF")
    upsert_device(
        "phone2",
        adb_serial="AAA111",
        screenshot_backend="scrcpy",
        input_backend="scrcpy",
    )
    yield db_path
    set_state_db_path_for_tests(None)


def _device(serial: str):
    return next(d for d in load_registry().devices if d.adb_serial == serial)


def test_set_screenshot_backend_adds_field(devices_db: Path) -> None:
    result = adb_api.set_device_backend("RF8RC00M8MF", screenshot_backend="scrcpy")
    assert result["ok"] is True
    assert result["screenshot_backend"] == "scrcpy"
    assert result["restart_required"] is True
    assert _device("RF8RC00M8MF").screenshot_backend == "scrcpy"


def test_set_input_backend_scrcpy(devices_db: Path) -> None:
    adb_api.set_device_backend("RF8RC00M8MF", input_backend="scrcpy")
    assert _device("RF8RC00M8MF").input_backend == "scrcpy"


def test_empty_string_removes_existing_field(devices_db: Path) -> None:
    """phone2 starts with both backends set; an empty value clears them."""
    adb_api.set_device_backend("AAA111", screenshot_backend="", input_backend="")
    phone = _device("AAA111")
    assert phone.screenshot_backend == ""
    assert phone.input_backend == ""


def test_other_fields_preserved(devices_db: Path) -> None:
    """The update must leave the unrelated device untouched."""
    phone_before = _device("AAA111")
    adb_api.set_device_backend("RF8RC00M8MF", input_backend="scrcpy")
    phone_after = _device("AAA111")
    assert phone_after.screenshot_backend == phone_before.screenshot_backend
    assert phone_after.input_backend == phone_before.input_backend
    assert phone_after.adb_serial == phone_before.adb_serial


def test_invalid_screenshot_backend_rejected(devices_db: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("RF8RC00M8MF", screenshot_backend="nonsense")
    assert exc.value.status_code == 400


def test_invalid_input_backend_rejected(devices_db: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("RF8RC00M8MF", input_backend="hyperdrive")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("RF8RC00M8MF", input_backend="minitouch")
    assert exc.value.status_code == 400


def test_unknown_serial_404(devices_db: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("ZZZ999", input_backend="adb")
    assert exc.value.status_code == 404


def test_none_field_leaves_value_alone(devices_db: Path) -> None:
    """Omitting input_backend (None) must not erase the existing value."""
    adb_api.set_device_backend("AAA111", screenshot_backend="adb")
    phone = _device("AAA111")
    assert phone.screenshot_backend == "adb"
    assert phone.input_backend == "scrcpy"  # left intact


def test_empty_serial_400(devices_db: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("  ", input_backend="adb")
    assert exc.value.status_code == 400


def test_get_adb_status_uses_effective_serial_for_name_only_devices(
    devices_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    upsert_device("127.0.0.1:5555")

    class Completed:
        returncode = 0
        stdout = "List of devices attached\n"
        stderr = ""

    def fake_run(*_args, **_kwargs):
        return Completed()

    monkeypatch.setattr(adb_api.subprocess, "run", fake_run)

    status = adb_api.get_adb_status()

    row = next(d for d in status["configured"] if d["name"] == "127.0.0.1:5555")
    assert row["adb_serial"] == "127.0.0.1:5555"
    assert row["screenshot_backend_effective"] == "quartz"
    assert row["input_backend_effective"] == "scrcpy"
