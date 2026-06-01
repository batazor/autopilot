"""Read raw notification state from a connected Android device via ADB."""

from __future__ import annotations

import shutil
import subprocess

from .logging_setup import get_logger

log = get_logger("adb")


class AdbError(RuntimeError):
    """Raised when the ADB command fails or is unavailable."""


def _base_cmd(adb_path: str, serial: str) -> list[str]:
    cmd = [adb_path]
    if serial.strip():
        cmd += ["-s", serial.strip()]
    return cmd


def adb_available(adb_path: str = "adb") -> bool:
    """True if the adb binary is resolvable on PATH or at the given path."""
    return shutil.which(adb_path) is not None or adb_path != "adb"


def list_devices(adb_path: str = "adb") -> list[str]:
    """Return serials of devices in the `device` state (best effort)."""
    try:
        out = subprocess.run(
            [adb_path, "devices"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("adb devices failed: %s", exc)
        return []
    serials = []
    for line in out.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            serials.append(parts[0])
    return serials


def dump_notifications(adb_path: str = "adb", serial: str = "", timeout: int = 20) -> str:
    """Run `adb shell dumpsys notification --noredact` and return stdout.

    ``--noredact`` keeps notification text un-truncated; if the device/Android
    version rejects it we retry without the flag.
    """
    base = _base_cmd(adb_path, serial)
    for args in (["shell", "dumpsys", "notification", "--noredact"],
                 ["shell", "dumpsys", "notification"]):
        try:
            proc = subprocess.run(
                base + args, capture_output=True, text=True, timeout=timeout
            )
        except FileNotFoundError as exc:
            msg = f"adb binary not found: {adb_path}"
            raise AdbError(msg) from exc
        except subprocess.TimeoutExpired as exc:
            msg = f"adb dumpsys timed out after {timeout}s"
            raise AdbError(msg) from exc
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
        log.debug("dumpsys variant %s rc=%s stderr=%s", args[-1], proc.returncode, proc.stderr.strip()[:200])
    msg = f"dumpsys notification failed (rc={proc.returncode}): {proc.stderr.strip()[:200]}"
    raise AdbError(msg)
