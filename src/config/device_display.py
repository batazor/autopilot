"""ADB device display profile — wm size/density, brightness, screen-on."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceDisplayConfig:
    """Optional display tweaks applied over ADB when the worker starts."""

    enabled: bool = True
    size: str | None = "720x1280"
    density: int | None = 320
    brightness_percent: int | None = 70
    heads_up_notifications: bool | None = False
    manual_brightness: bool | None = True
    keep_screen_on: bool | None = True
    screen_off_timeout_ms: int | None = 2_147_483_647
    wm_size_on_emulator: bool | None = None


def parse_device_display(raw: object) -> DeviceDisplayConfig | None:
    """Parse ``display:`` block from ``devices.yaml`` or ``worker.device_display``."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, dict):
        return None
    enabled = raw.get("enabled")
    if enabled is not None and not bool(enabled):
        return DeviceDisplayConfig(enabled=False)

    size = _optional_str(raw.get("size"))
    density = _optional_int(raw.get("density"))
    brightness = _optional_int(raw.get("brightness_percent"))
    heads_up = _optional_bool(raw.get("heads_up_notifications"))
    manual = _optional_bool(raw.get("manual_brightness"))
    keep_on = _optional_bool(raw.get("keep_screen_on"))
    screen_off_timeout_ms = _optional_int(raw.get("screen_off_timeout_ms"))
    wm_size_on_emulator = _optional_bool(raw.get("wm_size_on_emulator"))

    return DeviceDisplayConfig(
        enabled=True,
        size=size,
        density=density,
        brightness_percent=brightness,
        heads_up_notifications=heads_up,
        manual_brightness=manual,
        keep_screen_on=keep_on,
        screen_off_timeout_ms=screen_off_timeout_ms,
        wm_size_on_emulator=wm_size_on_emulator,
    )


def merge_device_display(
    worker_default: DeviceDisplayConfig | None,
    device_override: DeviceDisplayConfig | None,
) -> DeviceDisplayConfig | None:
    """Merge worker defaults with per-device overrides (device wins)."""
    if device_override is not None and not device_override.enabled:
        return None
    if worker_default is not None and not worker_default.enabled and device_override is None:
        return None
    base = worker_default or DeviceDisplayConfig()
    if device_override is None:
        return base
    return DeviceDisplayConfig(
        enabled=True,
        size=device_override.size if device_override.size is not None else base.size,
        density=device_override.density if device_override.density is not None else base.density,
        brightness_percent=(
            device_override.brightness_percent
            if device_override.brightness_percent is not None
            else base.brightness_percent
        ),
        heads_up_notifications=(
            device_override.heads_up_notifications
            if device_override.heads_up_notifications is not None
            else base.heads_up_notifications
        ),
        manual_brightness=(
            device_override.manual_brightness
            if device_override.manual_brightness is not None
            else base.manual_brightness
        ),
        keep_screen_on=(
            device_override.keep_screen_on
            if device_override.keep_screen_on is not None
            else base.keep_screen_on
        ),
        screen_off_timeout_ms=(
            device_override.screen_off_timeout_ms
            if device_override.screen_off_timeout_ms is not None
            else base.screen_off_timeout_ms
        ),
        wm_size_on_emulator=(
            device_override.wm_size_on_emulator
            if device_override.wm_size_on_emulator is not None
            else base.wm_size_on_emulator
        ),
    )


def _optional_str(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


def _optional_int(raw: object) -> int | None:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _optional_bool(raw: object) -> bool | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    s = str(raw).strip().lower()
    if s in {"1", "true", "yes", "on"}:
        return True
    if s in {"0", "false", "no", "off"}:
        return False
    return None
