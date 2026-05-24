"""ADB / devices.yaml API."""
from __future__ import annotations

import subprocess
from pathlib import Path  # noqa: TC003
from typing import Any

import yaml
from fastapi import HTTPException

from adb.controller import AdbController
from adb.minicap import get_minicap_status, install_minicap
from adb.minitouch import get_minitouch_status, install_minitouch
from adb.screencap import MSG_ADB_NOT_FOUND, resolve_adb_executable
from adb.serial import is_emulator_adb_serial
from config.loader import load_settings
from config.paths import repo_root

_REPO = repo_root()
_DEVICES = _REPO / "db" / "devices.yaml"
_SETTINGS = _REPO / "src" / "config" / "settings.yaml"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def get_adb_status() -> dict[str, Any]:
    settings = load_settings()
    devices_path = _DEVICES
    devices_raw = _load_yaml(devices_path)
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
    for d in devices_raw.get("devices", []) or []:
        if not isinstance(d, dict):
            continue
        explicit = str(d.get("screenshot_backend", "") or "").strip().lower()
        explicit_input = str(d.get("input_backend", "") or "").strip().lower()
        serial = str(d.get("adb_serial", "") or "")
        is_emu = is_emulator_adb_serial(serial)
        # Mirror dispatcher defaults.
        # Screenshot: smart per-serial (physical → minicap, emulator → quartz).
        # Input: always defaults to adb; minitouch is opt-in via devices.yaml
        # because it needs /dev/input access (root or accessible emulator).
        effective = explicit or ("quartz" if is_emu else "minicap")
        effective_input = explicit_input or "adb"
        configured.append(
            {
                "name": str(d.get("name", "") or ""),
                "adb_serial": serial,
                "instance_id": str(d.get("instance_id", "") or ""),
                "bluestacks_window_title": str(d.get("bluestacks_window_title", "") or ""),
                "screenshot_backend": explicit,
                "screenshot_backend_effective": effective,
                "input_backend": explicit_input,
                "input_backend_effective": effective_input,
            }
        )

    return {
        "adb_executable": adb_exe,
        "devices_yaml": str(devices_path.relative_to(_REPO)),
        "settings_yaml": str(_SETTINGS.relative_to(_REPO)),
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


_VALID_SCREENSHOT_BACKENDS = {"quartz", "adb", "minicap"}
_VALID_INPUT_BACKENDS = {"adb", "minitouch"}


def set_device_backend(
    serial: str,
    *,
    screenshot_backend: str | None = None,
    input_backend: str | None = None,
) -> dict[str, Any]:
    """Rewrite ``db/devices.yaml`` to set/clear the per-device backend fields.

    A value of ``""`` removes the explicit override (restores smart default).
    A value of ``None`` (parameter omitted) leaves that field untouched.

    Caveats:
    - ``load_settings()`` caches; running workers won't pick up the change until
      restart. The UI surfaces this via a follow-up "restart bot" hint.
    - We use ``yaml.safe_dump`` for the rewrite, which strips comments. The
      devices file is small enough that this is acceptable.
    """
    target = (serial or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="serial is required")
    if screenshot_backend is not None:
        normalized = screenshot_backend.strip().lower()
        if normalized and normalized not in _VALID_SCREENSHOT_BACKENDS:
            raise HTTPException(
                status_code=400,
                detail=f"screenshot_backend must be one of {sorted(_VALID_SCREENSHOT_BACKENDS)} or empty",
            )
        screenshot_backend = normalized
    if input_backend is not None:
        normalized = input_backend.strip().lower()
        if normalized and normalized not in _VALID_INPUT_BACKENDS:
            raise HTTPException(
                status_code=400,
                detail=f"input_backend must be one of {sorted(_VALID_INPUT_BACKENDS)} or empty",
            )
        input_backend = normalized

    if not _DEVICES.is_file():
        raise HTTPException(status_code=404, detail=f"{_DEVICES} not found")
    data = yaml.safe_load(_DEVICES.read_text(encoding="utf-8")) or {}
    devices = data.get("devices") or []
    if not isinstance(devices, list):
        raise HTTPException(status_code=500, detail="devices.yaml: 'devices' must be a list")

    matched: dict[str, Any] | None = None
    for entry in devices:
        if isinstance(entry, dict) and str(entry.get("adb_serial", "")) == target:
            matched = entry
            break
    if matched is None:
        raise HTTPException(status_code=404, detail=f"no device with adb_serial={target!r}")

    for field, value in (
        ("screenshot_backend", screenshot_backend),
        ("input_backend", input_backend),
    ):
        if value is None:
            continue
        if value:
            matched[field] = value
        else:
            matched.pop(field, None)

    _DEVICES.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return {
        "ok": True,
        "serial": target,
        "screenshot_backend": matched.get("screenshot_backend", ""),
        "input_backend": matched.get("input_backend", ""),
        "restart_required": True,
    }
