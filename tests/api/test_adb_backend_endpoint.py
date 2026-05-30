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


@pytest.fixture(autouse=True)
def no_auto_scrcpy_install(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        adb_api,
        "_auto_install_scrcpy_if_required",
        lambda *_args, **_kwargs: None,
    )


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


def test_register_device_creates_fleet_device_from_emulator_serial(
    devices_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_calls: list[tuple[str, str, str]] = []

    def fake_auto_install(
        serial: str,
        *,
        screenshot_backend: str,
        input_backend: str,
    ) -> dict[str, object]:
        install_calls.append((serial, screenshot_backend, input_backend))
        return {"ok": True, "serial": serial, "installed": True}

    monkeypatch.setattr(adb_api, "_auto_install_scrcpy_if_required", fake_auto_install)

    result = adb_api.register_device("127.0.0.1:5555")

    assert result == {
        "ok": True,
        "created": True,
        "name": "bs2",
        "adb_serial": "127.0.0.1:5555",
        "restart_required": False,
        "scrcpy_install": {
            "ok": True,
            "serial": "127.0.0.1:5555",
            "installed": True,
        },
    }
    assert install_calls == [("127.0.0.1:5555", "", "")]
    row = _device("127.0.0.1:5555")
    assert row.name == "bs2"


def test_register_device_reuses_existing_canonical_emulator_serial(
    devices_db: Path,
) -> None:
    upsert_device("bs3", adb_serial="127.0.0.1:5555")

    result = adb_api.register_device("emulator-5554")

    assert result["created"] is False
    assert result["name"] == "bs3"
    assert result["adb_serial"] == "127.0.0.1:5555"
    rows = [
        d
        for d in load_registry().devices
        if d.effective_serial == "127.0.0.1:5555"
    ]
    assert len(rows) == 1


def test_register_device_rejects_empty_serial(devices_db: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.register_device("  ")

    assert exc.value.status_code == 400


def test_set_device_backend_auto_installs_when_scrcpy_is_effective(
    devices_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_calls: list[tuple[str, str, str]] = []

    def fake_auto_install(
        serial: str,
        *,
        screenshot_backend: str,
        input_backend: str,
    ) -> dict[str, object]:
        install_calls.append((serial, screenshot_backend, input_backend))
        return {"ok": True, "serial": serial, "installed": True}

    monkeypatch.setattr(adb_api, "_auto_install_scrcpy_if_required", fake_auto_install)

    result = adb_api.set_device_backend(
        "RF8RC00M8MF",
        screenshot_backend="scrcpy",
        input_backend="scrcpy",
    )

    assert result["scrcpy_install"] == {
        "ok": True,
        "serial": "RF8RC00M8MF",
        "installed": True,
    }
    assert install_calls == [("RF8RC00M8MF", "scrcpy", "scrcpy")]


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


def test_get_adb_status_probes_default_emulator_tcp_port(
    devices_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    class Completed:
        def __init__(self, stdout: str = "List of devices attached\n") -> None:
            self.returncode = 0
            self.stdout = stdout
            self.stderr = ""

    def fake_run(args, **_kwargs):
        calls.append(list(args))
        if args[1:3] == ["devices", "-l"]:
            connected = any(call[1:3] == ["connect", "127.0.0.1:5555"] for call in calls)
            if connected:
                return Completed(
                    "List of devices attached\n"
                    "127.0.0.1:5555 device product:bluestacks\n"
                )
        return Completed()

    monkeypatch.setattr(adb_api.subprocess, "run", fake_run)

    status = adb_api.get_adb_status()

    assert any(call[1:3] == ["connect", "127.0.0.1:5555"] for call in calls)
    assert status["live_devices"] == [
        {
            "serial": "127.0.0.1:5555",
            "canonical_serial": "127.0.0.1:5555",
            "line": "127.0.0.1:5555 device product:bluestacks",
            "detected_games": [],
        }
    ]


def test_get_adb_status_reports_canonical_serial_for_emulator_alias(
    devices_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Completed:
        returncode = 0
        stdout = "List of devices attached\nemulator-5554 device product:sdk\n"
        stderr = ""

    def fake_run(*_args, **_kwargs):
        return Completed()

    monkeypatch.setattr(adb_api.subprocess, "run", fake_run)

    status = adb_api.get_adb_status()

    assert status["live_devices"][0]["serial"] == "emulator-5554"
    assert status["live_devices"][0]["canonical_serial"] == "127.0.0.1:5555"


def test_get_adb_status_marks_wos_beta_package(
    devices_db: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Completed:
        def __init__(self, stdout: str = "", returncode: int = 0) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    def fake_run(args, **_kwargs):
        if args[1:3] == ["devices", "-l"]:
            return Completed(
                "List of devices attached\n"
                "127.0.0.1:5625 device product:bluestacks\n"
            )
        if args[1:4] == ["-s", "127.0.0.1:5625", "shell"]:
            shell_args = args[4:]
            if shell_args == ["pm", "list", "packages"]:
                return Completed("package:com.xyz.gof\n")
            if shell_args == ["pidof", "com.xyz.gof"]:
                return Completed("1234\n")
            if shell_args[:2] == ["dumpsys", "activity"]:
                return Completed("topResumedActivity com.xyz.gof/.MainActivity\n")
            if shell_args[:2] == ["dumpsys", "window"]:
                return Completed("")
        return Completed(returncode=1)

    monkeypatch.setattr(adb_api.subprocess, "run", fake_run)
    monkeypatch.setattr(adb_api, "_probe_default_tcp_adb_targets", lambda *_args: False)

    status = adb_api.get_adb_status()

    assert status["live_devices"][0]["detected_games"] == [
        {
            "id": "wos",
            "label": "Whiteout Survival",
            "package": "com.xyz.gof",
            "beta": True,
            "running": True,
        }
    ]
