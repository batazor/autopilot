from __future__ import annotations

from unittest.mock import MagicMock

from adb.controller import AdbController
from adb.serial import is_emulator_adb_serial
from config.device_display import DeviceDisplayConfig, merge_device_display, parse_device_display


def test_is_emulator_adb_serial() -> None:
    assert is_emulator_adb_serial("127.0.0.1:5555") is True
    assert is_emulator_adb_serial("emulator-5554") is True
    assert is_emulator_adb_serial("RF8RC00M8MF") is False


def test_parse_device_display() -> None:
    cfg = parse_device_display(
        {
            "size": "720x1280",
            "density": 320,
            "brightness_percent": 80,
            "screen_off_timeout_ms": 123456,
        }
    )
    assert cfg is not None
    assert cfg.size == "720x1280"
    assert cfg.density == 320
    assert cfg.brightness_percent == 80
    assert cfg.screen_off_timeout_ms == 123456


def test_parse_device_display_disabled() -> None:
    cfg = parse_device_display({"enabled": False})
    assert cfg is not None
    assert cfg.enabled is False


def test_merge_device_display_device_overrides_worker() -> None:
    worker = DeviceDisplayConfig(brightness_percent=70, density=320)
    device = parse_device_display({"brightness_percent": 55})
    merged = merge_device_display(worker, device)
    assert merged is not None
    assert merged.brightness_percent == 55
    assert merged.density == 320


def test_parse_wm_size_on_emulator_false_string() -> None:
    cfg = parse_device_display({"wm_size_on_emulator": "false"})
    assert cfg is not None
    assert cfg.wm_size_on_emulator is False


def test_merge_wm_size_on_emulator_device_can_disable_worker_default() -> None:
    worker = DeviceDisplayConfig(wm_size_on_emulator=True)
    device = parse_device_display({"wm_size_on_emulator": False})
    merged = merge_device_display(worker, device)
    assert merged is not None
    assert merged.wm_size_on_emulator is False


def test_merge_wm_size_on_emulator_device_inherits_worker_true() -> None:
    worker = DeviceDisplayConfig(wm_size_on_emulator=True)
    device = parse_device_display({"brightness_percent": 55})
    merged = merge_device_display(worker, device)
    assert merged is not None
    assert merged.wm_size_on_emulator is True


def test_merge_device_display_disabled_on_device() -> None:
    worker = DeviceDisplayConfig()
    device = parse_device_display({"enabled": False})
    assert merge_device_display(worker, device) is None


def test_apply_display_config_skips_wm_on_emulator() -> None:
    ctrl = MagicMock(spec=AdbController)
    ctrl._serial = "127.0.0.1:5555"
    ctrl._shell = MagicMock()
    ctrl._screen_resolution = None
    cfg = DeviceDisplayConfig(
        size="720x1280",
        density=320,
        brightness_percent=70,
        keep_screen_on=True,
        screen_off_timeout_ms=2_147_483_647,
    )

    AdbController.apply_display_config(ctrl, cfg, serial="127.0.0.1:5555")

    wm_calls = [c for c in ctrl._shell.call_args_list if c.args[:2] == ("wm", "size")]
    assert wm_calls == []
    ctrl._shell.assert_any_call(
        "settings",
        "put",
        "system",
        "screen_brightness_mode",
        "0",
    )
    ctrl.set_brightness.assert_called_once_with(70)
    ctrl.set_heads_up_notifications.assert_called_once_with(enabled=False)
    ctrl._shell.assert_any_call(
        "settings",
        "put",
        "system",
        "screen_off_timeout",
        "2147483647",
    )
    ctrl._shell.assert_any_call("settings", "put", "global", "stay_on_while_plugged_in", "3")
    ctrl._shell.assert_any_call("svc", "power", "stayon", "true")


def test_apply_display_config_applies_auto_size_on_physical_device() -> None:
    ctrl = MagicMock(spec=AdbController)
    ctrl._serial = "RF8RC00M8MF"
    ctrl._shell = MagicMock()
    ctrl._screen_resolution = None
    ctrl._read_physical_wm_size = MagicMock(return_value=(1080, 2400))
    cfg = DeviceDisplayConfig(size="auto", density=320, brightness_percent=70)

    AdbController.apply_display_config(ctrl, cfg, serial="RF8RC00M8MF")

    ctrl._shell.assert_any_call("wm", "size", "720x1600")
    ctrl._shell.assert_any_call("wm", "density", "320")


def test_reset_display_overrides_clears_wm_cache() -> None:
    ctrl = MagicMock(spec=AdbController)
    ctrl._serial = "RF8RC00M8MF"
    ctrl._shell = MagicMock()
    ctrl._screen_resolution = (720, 1280)

    AdbController.reset_display_overrides(ctrl)

    ctrl._shell.assert_any_call("wm", "size", "reset")
    ctrl._shell.assert_any_call("wm", "density", "reset")
    ctrl.set_heads_up_notifications.assert_called_once_with(enabled=True)
    assert ctrl._screen_resolution is None


def test_apply_display_config_can_disable_keep_screen_on() -> None:
    ctrl = MagicMock(spec=AdbController)
    ctrl._serial = "127.0.0.1:5555"
    ctrl._shell = MagicMock()
    ctrl._screen_resolution = None
    cfg = DeviceDisplayConfig(keep_screen_on=False)

    AdbController.apply_display_config(ctrl, cfg, serial="127.0.0.1:5555")

    assert not any(c.args[:4] == ("settings", "put", "system", "screen_off_timeout") for c in ctrl._shell.call_args_list)
    assert not any(c.args[:3] == ("svc", "power", "stayon") for c in ctrl._shell.call_args_list)
