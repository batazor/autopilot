from __future__ import annotations

from typing import TYPE_CHECKING

from config.devices import load_devices
from config.loader import load_settings

if TYPE_CHECKING:
    from pathlib import Path


def test_devices_default_to_empty_screenshot_backend(tmp_path: Path) -> None:
    """Empty marker = smart default (physical → minicap, emulator → quartz)
    is chosen later by the dispatcher in bot_actions."""
    path = tmp_path / "devices.yaml"
    path.write_text(
        """
devices:
  - name: bs1
    adb_serial: 127.0.0.1:5555
    profiles: []
""",
        encoding="utf-8",
    )

    registry = load_devices(path)

    assert registry.devices[0].screenshot_backend == ""
    assert registry.devices[0].quartz_window_id is None
    assert registry.devices[0].quartz_crop is None


def test_devices_parse_minicap_screenshot_backend(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    path.write_text(
        """
devices:
  - name: phone
    adb_serial: RF8RC00M8MF
    screenshot_backend: minicap
    profiles: []
""",
        encoding="utf-8",
    )

    registry = load_devices(path)

    assert registry.devices[0].screenshot_backend == "minicap"


def test_devices_parse_explicit_screenshot_backend_and_quartz_hints(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    path.write_text(
        """
devices:
  - name: bs1
    adb_serial: 127.0.0.1:5555
    screenshot_backend: adb
    quartz_window_id: 122
    quartz_window_title: BlueStacks Air 0
    quartz_crop: [0, 65, 1012, 1798]
    profiles: []
""",
        encoding="utf-8",
    )

    registry = load_devices(path)
    device = registry.devices[0]

    assert device.screenshot_backend == "adb"
    assert device.quartz_window_id == 122
    assert device.quartz_window_title == "BlueStacks Air 0"
    assert device.quartz_crop == (0, 65, 1012, 1798)


def test_settings_instances_include_screenshot_backend(tmp_path: Path, monkeypatch) -> None:
    import config.paths

    monkeypatch.setattr(config.paths, "repo_root", lambda: tmp_path)
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text("redis:\n  url: redis://localhost:6379/0\n", encoding="utf-8")
    devices_path = tmp_path / "db" / "devices.yaml"
    devices_path.parent.mkdir()
    devices_path.write_text(
        """
devices:
  - name: bs1
    adb_serial: 127.0.0.1:5555
    screenshot_backend: adb
    profiles: []
""",
        encoding="utf-8",
    )

    settings = load_settings(settings_path)

    assert settings.instances[0].screenshot_backend == "adb"


def test_load_devices_creates_empty_registry_file_on_first_boot(tmp_path: Path, monkeypatch) -> None:
    import config.paths

    monkeypatch.setattr(config.paths, "repo_root", lambda: tmp_path)
    devices_path = tmp_path / "db" / "devices.yaml"
    assert not devices_path.exists()

    registry = load_devices()

    assert registry.devices == []
    assert devices_path.read_text(encoding="utf-8") == "devices: []\n"


def test_load_devices_missing_explicit_path_returns_empty_without_creating(tmp_path: Path) -> None:
    path = tmp_path / "devices.yaml"
    assert not path.exists()

    registry = load_devices(path)

    assert registry.devices == []
    assert not path.exists()
