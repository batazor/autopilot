"""ADB / devices API — backs the Next.js /adb page."""
from __future__ import annotations

import socket
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
from config.games import GAMES
from config.loader import load_settings
from config.paths import repo_root

_REPO = repo_root()
# Display-only label. The settings used to live in ``src/config/settings.yaml``;
# they're now baked into :mod:`config._settings_data` so the UI just shows where
# the *historical* file lived (the dashboard's ADB page renders this as info).
_SETTINGS_DISPLAY = "src/config/_settings_data.py"
_DEVICES_DB_REL = "db/state/state.db"
# Emulator ADB ports are allocated in steps of 10 starting at 5555
# (5555, 5565, ... 5625). Probe the whole range so newly-added instances are
# auto-discovered without hardcoding each port. The /adb page can override the
# bounds per scan; these stay the default when it doesn't.
_ADB_TCP_PORT_RANGE = range(5555, 5626, 10)
_ADB_TCP_PORT_DEFAULT_START = 5555
_ADB_TCP_PORT_DEFAULT_END = 5625
_ADB_TCP_PORT_DEFAULT_STEP = 10
# Guard rails so a user-supplied range can't spawn thousands of sockets/threads.
_ADB_TCP_PORT_SCAN_CAP = 256
_ADB_TCP_PROBE_MAX_WORKERS = 64


def build_tcp_port_range(
    start: int | None = None,
    end: int | None = None,
    step: int | None = None,
) -> list[int]:
    """Resolve the list of TCP ports to probe for emulator ADB endpoints.

    Unset bounds fall back to the default emulator allocation
    (``5555..5625`` step ``10``). Values are clamped to valid TCP ports, the
    bounds are reordered if inverted, and the result is capped at
    ``_ADB_TCP_PORT_SCAN_CAP`` so a pathological range can't tie up the scan.
    """
    lo = _ADB_TCP_PORT_DEFAULT_START if start is None else int(start)
    hi = _ADB_TCP_PORT_DEFAULT_END if end is None else int(end)
    stride = _ADB_TCP_PORT_DEFAULT_STEP if step is None else int(step)

    lo = max(1, min(lo, 65535))
    hi = max(1, min(hi, 65535))
    if hi < lo:
        lo, hi = hi, lo
    stride = max(1, stride)

    return list(range(lo, hi + 1, stride))[:_ADB_TCP_PORT_SCAN_CAP]


def _parse_adb_devices(stdout: str) -> list[dict[str, Any]]:
    live: list[dict[str, Any]] = []
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


def _scan_adb_devices(adb_exe: str) -> tuple[list[dict[str, Any]], str | None]:
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


def _has_live_adb_serial(live: list[dict[str, Any]], serial: str) -> bool:
    target = canonical_adb_serial(serial)
    return any(
        canonical_adb_serial(row.get("canonical_serial") or row.get("serial") or "")
        == target
        for row in live
    )


def _port_open(port: int, timeout: float = 0.3) -> bool:
    """Cheap TCP liveness check so we only ``adb connect`` ports that listen."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _probe_default_tcp_adb_targets(
    adb_exe: str,
    live: list[dict[str, Any]],
    ports: list[int] | None = None,
) -> bool:
    if ports is None:
        ports = list(_ADB_TCP_PORT_RANGE)
    candidates = [
        port
        for port in ports
        if not _has_live_adb_serial(live, f"127.0.0.1:{port}")
    ]
    if not candidates:
        return False

    # Probe liveness in parallel (sub-second) instead of letting ``adb connect``
    # block ~5s per dead port. This keeps the /adb status endpoint snappy no
    # matter how wide the port range grows.
    with ThreadPoolExecutor(
        max_workers=min(len(candidates), _ADB_TCP_PROBE_MAX_WORKERS)
    ) as pool:
        open_ports = [
            port
            for port, is_open in zip(
                candidates, pool.map(_port_open, candidates), strict=True
            )
            if is_open
        ]

    for port in open_ports:
        try:
            subprocess.run(
                [adb_exe, "connect", f"127.0.0.1:{port}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            # Best-effort discovery only. A refused localhost port should not
            # make a USB/physical device scan look broken.
            continue
    return bool(open_ports)


def _adb_shell_text(
    adb_exe: str,
    serial: str,
    *args: str,
    timeout: float = 4.0,
) -> str:
    try:
        proc = subprocess.run(
            [adb_exe, "-s", serial, "shell", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception:
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout or ""


def _parse_pm_packages(stdout: str) -> set[str]:
    packages: set[str] = set()
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line.startswith("package:"):
            continue
        package = line.removeprefix("package:").strip()
        if "=" in package:
            package = package.rsplit("=", 1)[-1].strip()
        if package:
            packages.add(package)
    return packages


def _detect_known_games(adb_exe: str, serial: str) -> list[dict[str, Any]]:
    installed = _parse_pm_packages(
        _adb_shell_text(adb_exe, serial, "pm", "list", "packages")
    )
    if not installed:
        return []

    foreground_text = "\n".join(
        [
            _adb_shell_text(adb_exe, serial, "dumpsys", "activity", "activities"),
            _adb_shell_text(adb_exe, serial, "dumpsys", "window"),
        ]
    )

    detected: list[dict[str, Any]] = []
    for gid, spec in GAMES.items():
        for package in spec.packages:
            if package not in installed:
                continue
            running = bool(
                _adb_shell_text(adb_exe, serial, "pidof", package, timeout=2.0).strip()
                or package in foreground_text
            )
            detected.append(
                {
                    "id": gid,
                    "label": spec.label,
                    "package": package,
                    "beta": package != spec.package,
                    "running": running,
                }
            )
    return detected


def get_adb_status(
    port_start: int | None = None,
    port_end: int | None = None,
    port_step: int | None = None,
) -> dict[str, Any]:
    settings = load_settings()
    adb_exe = str(settings.worker.adb_executable or "adb")
    ports = build_tcp_port_range(port_start, port_end, port_step)
    live: list[dict[str, Any]]
    scan_error: str | None = None
    try:
        live, scan_error = _scan_adb_devices(adb_exe)
        if scan_error is None and _probe_default_tcp_adb_targets(adb_exe, live, ports):
            refreshed_live, refreshed_error = _scan_adb_devices(adb_exe)
            if refreshed_error is None:
                live = refreshed_live
            elif not live:
                scan_error = refreshed_error
    except Exception as exc:
        live = []
        scan_error = str(exc)

    for row in live:
        row["detected_games"] = _detect_known_games(adb_exe, str(row["serial"]))

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
        "scan_port_range": {
            "start": ports[0] if ports else None,
            "end": ports[-1] if ports else None,
            "step": port_step if port_step is not None else _ADB_TCP_PORT_DEFAULT_STEP,
            "count": len(ports),
        },
    }


def _notify_supervisor_reconcile(reason: str) -> None:
    """Wake a running worker supervisor so it picks up the registry change without
    a restart. Best-effort: if Redis is down or no bot is running, the next
    Start still converges from the persisted registry."""
    try:
        from dashboard.dashboard_events import publish_device_reconcile
        from dashboard.redis_client import get_redis

        publish_device_reconcile(get_redis(), reason=reason)
    except Exception:  # pragma: no cover - notification is best-effort
        pass


def request_device_reconcile(reason: str = "manual") -> dict[str, Any]:
    """Explicit UI/API command: ask the running supervisor to reconcile devices."""
    clean = (reason or "manual").strip() or "manual"
    _notify_supervisor_reconcile(clean)
    return {"ok": True, "reason": clean}


def _serial_matches(a: str, b: str) -> bool:
    return canonical_adb_serial(a) == canonical_adb_serial(b)


def _effective_backends(
    serial: str,
    *,
    screenshot_backend: str,
    input_backend: str,
) -> tuple[str, str]:
    # Mirror dispatcher defaults.
    # Screenshot: defaults to scrcpy for every device; adb is an explicit
    # compatibility override.
    # Input: defaults to scrcpy; adb is an explicit compatibility override.
    screenshot = (screenshot_backend or "").strip().lower() or "scrcpy"
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
            _notify_supervisor_reconcile(f"register:{device.name}")
            return {
                "ok": True,
                "created": False,
                "name": device.name,
                "adb_serial": device.effective_serial,
                "restart_required": False,
                "scrcpy_install": scrcpy_install,
            }

    name = _next_device_name(adb_serial)
    try:
        upsert_device(name, adb_serial=adb_serial)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    invalidate_device_registry()
    # Nudge a running supervisor to spawn this device's worker right away; no
    # bot restart needed.
    _notify_supervisor_reconcile(f"register:{name}")
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
        "restart_required": False,
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
    "request_device_reconcile",
    "reset_device_display",
    "set_device_backend",
]
