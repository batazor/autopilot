"""Baked-in defaults for the bot.

Previously lived in ``settings.yaml`` next to this module. Hoisted into Python
so Nuitka compilation absorbs the values into the resulting ``config.so``,
leaving no readable configuration on disk. Env-var overrides (``WOS_*``) still
work via :func:`config.loader.load_settings` — the dict here is just the
fallback when no override is set.

Edit cadence: rare. Anything operator-tunable should go through env vars or
``db/devices.yaml`` (the latter is still a YAML file because users add their
own emulators / accounts there).
"""
from __future__ import annotations

from typing import Any

SETTINGS: dict[str, Any] = {
    "redis": {
        "url": "redis://localhost:6379/0",
        "key_prefix": "wos",
    },
    "ocr": {
        "lang": "eng",
        "tesseract_cmd": "tesseract",
        "tessdata_dir": "",
        "timeout_seconds": 10,
    },
    "scheduler": {
        "interval_seconds": 30,
        "ortools_timeout_seconds": 1.0,
    },
    "worker": {
        # adb path; align with UI adb override when needed.
        "adb_executable": "",
        # Applied over ADB when the worker starts (per-device overrides in
        # ``db/devices.yaml`` → ``display:``). wm size/density are skipped
        # for localhost emulators unless ``wm_size_on_emulator: true``.
        "device_display": {
            "size": "720x1280",
            "density": 320,
            "brightness_percent": 70,
            "keep_screen_on": True,
            # Android's normal "screen timeout" can dim the game before sleep;
            # keep it effectively disabled while the bot controls the device.
            "screen_off_timeout_ms": 2147483647,
        },
        # ADB foreground check interval for ``worker.game_health_watchdog``
        # (separate subprocess).
        "health_check_interval_seconds": 15,
        "restart_wait_seconds": 10,
        "task_timeout_seconds": 300,
        # Max wait at worker boot for Whiteout to reach foreground.
        "game_foreground_timeout_seconds": 120,
        "overlay_analyze_when_busy": False,
        "screen_detect_when_busy": False,
        "device_reference_snapshot_interval_seconds": 1.0,
    },
}
