"""ADB / devices API — backs the Next.js /adb page."""
from __future__ import annotations

import subprocess
from typing import Any

from fastapi import HTTPException

from adb.controller import AdbController
from adb.minicap import get_minicap_status, install_minicap
from adb.minitouch import get_minitouch_status, install_minitouch
from adb.scrcpy import get_scrcpy_status, install_scrcpy
from adb.screencap import MSG_ADB_NOT_FOUND, resolve_adb_executable
from adb.serial import is_emulator_adb_serial
from config.devices_db import (
    VALID_INPUT_BACKENDS,
    VALID_SCREENSHOT_BACKENDS,
    load_registry,
)
from config.devices_db import set_device_backend as db_set_device_backend
from config.loader import load_settings
from config.paths import repo_root

_REPO = repo_root()
# Display-only label. The settings used to live in ``src/config/settings.yaml``;
# they're now baked into :mod:`config._settings_data` so the UI just shows where
# the *historical* file lived (the dashboard's ADB page renders this as info).
_SETTINGS_DISPLAY = "src/config/_settings_data.py"
_DEVICES_DB_REL = "db/state/state.db"


def get_adb_status() -> dict[str, Any]:
    settings = load_settings()
    adb_exe = str(settings.worker.adb_executable or "adb")
    live: list[dict[str, str]] = []
    scan_error: str | None = None
    try:
        proc = subprocess.run(
            [adb_exe, "devices", "-l"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode != 0:
            scan_error = (proc.stderr or proc.stdout or "adb failed").strip()
        else:
            for line in (proc.stdout or "").splitlines():
                line = line.strip()
                if not line or line.startswith("List of devices"):
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    live.append({"serial": parts[0], "line": line})
    except Exception as exc:
        scan_error = str(exc)

    configured: list[dict[str, Any]] = []
    for device in load_registry().devices:
        serial = device.effective_serial
        is_emu = is_emulator_adb_serial(serial)
        # Mirror dispatcher defaults.
        # Screenshot: smart per-serial (physical → minicap, emulator → quartz).
        # Input: always defaults to adb; minitouch is opt-in via the UI editor
        # because it needs /dev/input access (root or accessible emulator).
        effective = device.screenshot_backend or ("quartz" if is_emu else "minicap")
        effective_input = device.input_backend or "adb"
        configured.append(
            {
                "name": device.name,
                "adb_serial": serial,
                "instance_id": "",
                "bluestacks_window_title": "",
                "screenshot_backend": device.screenshot_backend,
                "screenshot_backend_effective": effective,
                "input_backend": device.input_backend,
                "input_backend_effective": effective_input,
            }
        )

    return {
        "adb_executable": adb_exe,
        "devices_yaml": _DEVICES_DB_REL,
        "settings_yaml": _SETTINGS_DISPLAY,
        "configured": configured,
        "live_devices": live,
        "scan_error": scan_error,
    }


def reset_device_display(serial: str) -> dict[str, Any]:
    """Reset wm size/density and re-enable heads-up notifications on a device."""
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")

    settings = load_settings()
    adb_exe = str(settings.worker.adb_executable or "adb")
    resolved = resolve_adb_executable(adb_exe)
    if resolved is None:
        raise HTTPException(status_code=503, detail=MSG_ADB_NOT_FOUND)

    try:
        ctrl = AdbController("_api_", target, adb_bin=resolved)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    ctrl.reset_display_overrides()
    wm_size = ctrl._shell("wm", "size")
    wm_density = ctrl._shell("wm", "density")
    return {
        "ok": True,
        "serial": target,
        "wm_size": wm_size,
        "wm_density": wm_density,
    }


def _resolve_adb_or_raise() -> str:
    settings = load_settings()
    adb_exe = str(settings.worker.adb_executable or "adb")
    resolved = resolve_adb_executable(adb_exe)
    if resolved is None:
        raise HTTPException(status_code=503, detail=MSG_ADB_NOT_FOUND)
    return resolved


def get_minicap_status_for(serial: str) -> dict[str, Any]:
    """Probe device for installed minicap binary + library."""
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")
    resolved = _resolve_adb_or_raise()
    status = get_minicap_status(target, resolved)
    return status.to_dict()


def install_minicap_for(serial: str) -> dict[str, Any]:
    """Download matching prebuilts (DeviceFarmer/minicap) and push to /data/local/tmp."""
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")
    resolved = _resolve_adb_or_raise()
    status = install_minicap(target, resolved)
    payload = status.to_dict()
    payload["ok"] = status.installed
    return payload


def get_minitouch_status_for(serial: str) -> dict[str, Any]:
    """Probe device for installed minitouch binary."""
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")
    resolved = _resolve_adb_or_raise()
    status = get_minitouch_status(target, resolved)
    return status.to_dict()


def install_minitouch_for(serial: str) -> dict[str, Any]:
    """Download matching minitouch prebuilt (openatx/stf-binaries) and push."""
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")
    resolved = _resolve_adb_or_raise()
    status = install_minitouch(target, resolved)
    payload = status.to_dict()
    payload["ok"] = status.installed
    return payload


def get_scrcpy_status_for(serial: str) -> dict[str, Any]:
    """Probe device for installed scrcpy-server jar."""
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")
    resolved = _resolve_adb_or_raise()
    status = get_scrcpy_status(target, resolved)
    return status.to_dict()


def install_scrcpy_for(serial: str) -> dict[str, Any]:
    """Download scrcpy-server jar from Genymobile/scrcpy GitHub release and push."""
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")
    resolved = _resolve_adb_or_raise()
    status = install_scrcpy(target, resolved)
    payload = status.to_dict()
    payload["ok"] = status.installed
    return payload


def set_device_backend(
    serial: str,
    *,
    screenshot_backend: str | None = None,
    input_backend: str | None = None,
) -> dict[str, Any]:
    """Update per-device backend fields in SQLite.

    Empty string clears an override (smart default kicks in); ``None`` leaves
    the field untouched. ``load_settings()`` caches the registry — running
    workers must restart to observe the change.
    """
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")

    # Find the device row by adb_serial; surface a 404 the way clients expect.
    registry = load_registry()
    matched = next(
        (d for d in registry.devices if d.adb_serial == target or d.name == target),
        None,
    )
    if matched is None:
        raise HTTPException(status_code=404, detail=f"no device with adb_serial={target!r}")

    try:
        new_screenshot, new_input = db_set_device_backend(
            matched.name,
            screenshot_backend=screenshot_backend,
            input_backend=input_backend,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "ok": True,
        "serial": target,
        "screenshot_backend": new_screenshot,
        "input_backend": new_input,
        "restart_required": True,
    }


# Re-export so the router & tests don't need to import from two places.
__all__ = [
    "VALID_INPUT_BACKENDS",
    "VALID_SCREENSHOT_BACKENDS",
    "get_adb_status",
    "get_minicap_status_for",
    "get_minitouch_status_for",
    "install_minicap_for",
    "install_minitouch_for",
    "reset_device_display",
    "set_device_backend",
]
