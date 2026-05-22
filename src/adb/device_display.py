"""Apply ``DeviceDisplayConfig`` over ADB shell/settings."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from adb.controller import AdbController
    from config.device_display import DeviceDisplayConfig


def apply_device_display_config(
    controller: AdbController,
    *,
    serial: str,
    config: DeviceDisplayConfig,
) -> None:
    """Push wm size/density, brightness, and related settings to the device."""
    controller.apply_display_config(config, serial=serial)


def reset_device_display_overrides(controller: AdbController, *, serial: str) -> None:
    """Clear wm size/density overrides and restore heads-up notifications on ``serial``."""
    prev = controller._serial
    try:
        controller.set_active_device(serial)
        controller.reset_display_overrides()
    finally:
        controller.set_active_device(prev)
