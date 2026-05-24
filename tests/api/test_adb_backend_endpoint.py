"""Tests for set_device_backend (devices.yaml rewrite)."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml
from fastapi import HTTPException

from api.services import adb_api

if TYPE_CHECKING:
    from pathlib import Path

_BASE_YAML = """\
devices:
- name: bs1
  adb_serial: RF8RC00M8MF
  display:
    size: 720x1280
    density: 320
    brightness_percent: 70
  profiles:
  - email: ''
    gamer:
    - id: 401227964
      nickname: batazor
    - id: 765502864
      nickname: lord765502864
- name: phone2
  adb_serial: AAA111
  screenshot_backend: minicap
  input_backend: minitouch
  profiles: []
"""


@pytest.fixture
def devices_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "devices.yaml"
    path.write_text(_BASE_YAML, encoding="utf-8")
    monkeypatch.setattr(adb_api, "_DEVICES", path)
    return path


def _read(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_set_screenshot_backend_adds_field(devices_yaml: Path) -> None:
    result = adb_api.set_device_backend("RF8RC00M8MF", screenshot_backend="minicap")

    assert result["ok"] is True
    assert result["screenshot_backend"] == "minicap"
    assert result["restart_required"] is True
    data = _read(devices_yaml)
    bs1 = next(d for d in data["devices"] if d["adb_serial"] == "RF8RC00M8MF")
    assert bs1["screenshot_backend"] == "minicap"


def test_set_input_backend_minitouch(devices_yaml: Path) -> None:
    adb_api.set_device_backend("RF8RC00M8MF", input_backend="minitouch")
    data = _read(devices_yaml)
    bs1 = next(d for d in data["devices"] if d["adb_serial"] == "RF8RC00M8MF")
    assert bs1["input_backend"] == "minitouch"


def test_empty_string_removes_existing_field(devices_yaml: Path) -> None:
    """phone2 starts with both backends set; an empty value clears them."""
    adb_api.set_device_backend("AAA111", screenshot_backend="", input_backend="")
    data = _read(devices_yaml)
    phone = next(d for d in data["devices"] if d["adb_serial"] == "AAA111")
    assert "screenshot_backend" not in phone
    assert "input_backend" not in phone


def test_other_fields_preserved(devices_yaml: Path) -> None:
    """The rewrite must leave display, profiles, and other devices untouched."""
    before = _read(devices_yaml)
    adb_api.set_device_backend("RF8RC00M8MF", input_backend="minitouch")
    after = _read(devices_yaml)

    bs1_before = next(d for d in before["devices"] if d["adb_serial"] == "RF8RC00M8MF")
    bs1_after = next(d for d in after["devices"] if d["adb_serial"] == "RF8RC00M8MF")
    assert bs1_after["display"] == bs1_before["display"]
    assert bs1_after["profiles"] == bs1_before["profiles"]
    # The other device must be byte-for-byte identical to before.
    assert next(d for d in after["devices"] if d["adb_serial"] == "AAA111") == next(
        d for d in before["devices"] if d["adb_serial"] == "AAA111"
    )


def test_invalid_screenshot_backend_rejected(devices_yaml: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("RF8RC00M8MF", screenshot_backend="nonsense")
    assert exc.value.status_code == 400


def test_invalid_input_backend_rejected(devices_yaml: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("RF8RC00M8MF", input_backend="hyperdrive")
    assert exc.value.status_code == 400


def test_unknown_serial_404(devices_yaml: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("ZZZ999", input_backend="adb")
    assert exc.value.status_code == 404


def test_none_field_leaves_value_alone(devices_yaml: Path) -> None:
    """Omitting input_backend (None) must not erase the existing value."""
    adb_api.set_device_backend("AAA111", screenshot_backend="adb")
    data = _read(devices_yaml)
    phone = next(d for d in data["devices"] if d["adb_serial"] == "AAA111")
    assert phone["screenshot_backend"] == "adb"
    assert phone["input_backend"] == "minitouch"  # left intact


def test_empty_serial_400(devices_yaml: Path) -> None:
    with pytest.raises(HTTPException) as exc:
        adb_api.set_device_backend("  ", input_backend="adb")
    assert exc.value.status_code == 400
