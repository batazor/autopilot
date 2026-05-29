"""ADB / devices API — backs the Next.js /adb page."""
from __future__ import annotations

import subprocess
from typing import Any

from fastapi import HTTPException

from adb.controller import AdbController
from adb.scrcpy import get_scrcpy_status, install_scrcpy
from adb.screencap import MSG_ADB_NOT_FOUND, resolve_adb_executable
from adb.serial import canonical_adb_serial, is_emulator_adb_serial
from config.devices import invalidate_device_registry
from config.devices_db import (
    VALID_INPUT_BACKENDS,
    VALID_SCREENSHOT_BACKENDS,
    load_registry,
    upsert_device,
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
_DEFAULT_TCP_ADB_TARGETS = ("127.0.0.1:5555",)


def _parse_adb_devices(stdout: str) -> list[dict[str, str]]:
    live: list[dict[str, str]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serial = parts[0]
            live.append(
                {
                    "serial": serial,
                    "canonical_serial": canonical_adb_serial(serial),
                    "line": line,
                }
            )
    return live


def _scan_adb_devices(adb_exe: str) -> tuple[list[dict[str, str]], str | None]:
    proc = subprocess.run(
        [adb_exe, "devices", "-l"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if proc.returncode != 0:
        return [], (proc.stderr or proc.stdout or "adb failed").strip()
    return _parse_adb_devices(proc.stdout or ""), None


def _has_live_adb_serial(live: list[dict[str, str]], serial: str) -> bool:
    target = canonical_adb_serial(serial)
    return any(
        canonical_adb_serial(row.get("canonical_serial") or row.get("serial") or "")
        == target
        for row in live
    )


def _probe_default_tcp_adb_targets(adb_exe: str, live: list[dict[str, str]]) -> bool:
    attempted = False
    for target in _DEFAULT_TCP_ADB_TARGETS:
        if _has_live_adb_serial(live, target):
            continue
        attempted = True
        try:
            subprocess.run(
                [adb_exe, "connect", target],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            # Best-effort discovery only. A refused localhost port should not
            # make a USB/physical device scan look broken.
            continue
    return attempted


def get_adb_status() -> dict[str, Any]:
    settings = load_settings()
    adb_exe = str(settings.worker.adb_executable or "adb")
    live: list[dict[str, str]]
    scan_error: str | None = None
    try:
        live, scan_error = _scan_adb_devices(adb_exe)
        if scan_error is None and _probe_default_tcp_adb_targets(adb_exe, live):
            refreshed_live, refreshed_error = _scan_adb_devices(adb_exe)
            if refreshed_error is None:
                live = refreshed_live
            elif not live:
                scan_error = refreshed_error
    except Exception as exc:
        live = []
        scan_error = str(exc)

    configured: list[dict[str, Any]] = []
    for device in load_registry().devices:
        serial = device.effective_serial
        effective, effective_input = _effective_backends(
            serial,
            screenshot_backend=device.screenshot_backend,
            input_backend=device.input_backend,
        )
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


def _serial_matches(a: str, b: str) -> bool:
    return canonical_adb_serial(a) == canonical_adb_serial(b)


def _effective_backends(
    serial: str,
    *,
    screenshot_backend: str,
    input_backend: str,
) -> tuple[str, str]:
    # Mirror dispatcher defaults.
    # Screenshot: smart per-serial (physical → scrcpy, emulator → quartz).
    # Input: defaults to scrcpy; adb is an explicit compatibility override.
    screenshot = (screenshot_backend or "").strip().lower() or (
        "quartz" if is_emulator_adb_serial(serial) else "scrcpy"
    )
    input_ = (input_backend or "").strip().lower() or "scrcpy"
    return screenshot, input_


def _uses_scrcpy_backend(
    serial: str,
    *,
    screenshot_backend: str,
    input_backend: str,
) -> bool:
    screenshot, input_ = _effective_backends(
        serial,
        screenshot_backend=screenshot_backend,
        input_backend=input_backend,
    )
    return "scrcpy" in {screenshot, input_}


def _next_device_name(serial: str) -> str:
    registry = load_registry()
    target = canonical_adb_serial(serial)
    for device in registry.devices:
        if _serial_matches(device.effective_serial, target):
            return device.name

    prefix = "bs" if is_emulator_adb_serial(target) else "device"
    used = {device.name for device in registry.devices}
    i = 1
    while f"{prefix}{i}" in used:
        i += 1
    return f"{prefix}{i}"


def register_device(serial: str) -> dict[str, Any]:
    """Persist a live ADB serial into the device registry."""
    raw = (serial or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="serial is required")

    adb_serial = canonical_adb_serial(raw)
    registry = load_registry()
    for device in registry.devices:
        if _serial_matches(device.effective_serial, adb_serial):
            scrcpy_install = _auto_install_scrcpy_if_required(
                device.effective_serial,
                screenshot_backend=device.screenshot_backend,
                input_backend=device.input_backend,
            )
            return {
                "ok": True,
                "created": False,
                "name": device.name,
                "adb_serial": device.effective_serial,
                "restart_required": True,
                "scrcpy_install": scrcpy_install,
            }

    name = _next_device_name(adb_serial)
    try:
        upsert_device(name, adb_serial=adb_serial)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    invalidate_device_registry()
    scrcpy_install = _auto_install_scrcpy_if_required(
        adb_serial,
        screenshot_backend="",
        input_backend="",
    )
    return {
        "ok": True,
        "created": True,
        "name": name,
        "adb_serial": adb_serial,
        "restart_required": True,
        "scrcpy_install": scrcpy_install,
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


def _auto_install_scrcpy_if_required(
    serial: str,
    *,
    screenshot_backend: str,
    input_backend: str,
) -> dict[str, Any] | None:
    if not _uses_scrcpy_backend(
        serial,
        screenshot_backend=screenshot_backend,
        input_backend=input_backend,
    ):
        return None
    try:
        resolved = _resolve_adb_or_raise()
        status = install_scrcpy(serial, resolved)
        payload = status.to_dict()
        payload["ok"] = bool(status.installed and not status.last_error)
        return payload
    except HTTPException as exc:
        return {
            "ok": False,
            "serial": serial,
            "installed": False,
            "last_error": str(exc.detail),
        }
    except Exception as exc:
        return {
            "ok": False,
            "serial": serial,
            "installed": False,
            "last_error": str(exc),
        }


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
    payload["ok"] = bool(status.installed and not status.last_error)
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
    scrcpy_install = _auto_install_scrcpy_if_required(
        matched.effective_serial,
        screenshot_backend=new_screenshot,
        input_backend=new_input,
    )

    return {
        "ok": True,
        "serial": target,
        "screenshot_backend": new_screenshot,
        "input_backend": new_input,
        "restart_required": True,
        "scrcpy_install": scrcpy_install,
    }


# Re-export so the router & tests don't need to import from two places.
__all__ = [
    "VALID_INPUT_BACKENDS",
    "VALID_SCREENSHOT_BACKENDS",
    "get_adb_status",
    "register_device",
    "reset_device_display",
    "set_device_backend",
]
