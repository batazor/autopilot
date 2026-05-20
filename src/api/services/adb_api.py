"""ADB / devices.yaml API."""
from __future__ import annotations

import subprocess
from pathlib import Path  # noqa: TC003
from typing import Any

import yaml

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
