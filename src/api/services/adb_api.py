"""ADB / devices.yaml API."""
from __future__ import annotations

import subprocess
from pathlib import Path  # noqa: TC003
from typing import Any

import yaml
from fastapi import HTTPException

from adb.controller import AdbController
from adb.screencap import MSG_ADB_NOT_FOUND, resolve_adb_executable
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
        configured.append(
            {
                "name": str(d.get("name", "") or ""),
                "adb_serial": str(d.get("adb_serial", "") or ""),
                "instance_id": str(d.get("instance_id", "") or ""),
                "bluestacks_window_title": str(d.get("bluestacks_window_title", "") or ""),
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
    """Run ``wm size reset`` and ``wm density reset`` on a connected device."""
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
