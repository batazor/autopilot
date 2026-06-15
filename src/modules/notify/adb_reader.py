"""Read raw notification state from a connected Android device via ADB."""

from __future__ import annotations

import re
import shutil
import subprocess

from .logging_setup import get_logger

log = get_logger("adb")

# Notification keys look like ``0|com.gof.global|5|null|10080``. We hand the key
# single-quoted to the *device* shell (the ``|`` must not be parsed as a pipe),
# so it must not itself contain a single quote, whitespace, or control chars.
# Keys for the game packages we monitor are always this plain shape; anything
# else is rejected rather than risk shell injection.
_SAFE_KEY_RE = re.compile(r"^[\w.|/@:#=+-]+$")


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


def snooze_notification(
    key: str,
    *,
    duration_ms: int,
    adb_path: str = "adb",
    serial: str = "",
    timeout: int = 15,
) -> bool:
    """Snooze (dismiss from the shade) a posted notification by its key.

    Android has no public shell command to truly *cancel* another app's
    notification, so this uses ``cmd notification snooze --for <ms> <key>`` — the
    closest public mechanism. A snoozed notification leaves the shade and the
    active ``dumpsys`` record list (so :func:`dump_notifications` stops returning
    it) until the snooze expires; at a multi-day duration that's effectively a
    dismissal.

    Best-effort: returns ``True`` on a clean ``rc==0``, ``False`` otherwise
    (unsafe/empty key, adb missing, non-zero exit, timeout). Never raises — a
    failed dismissal must not abort the poll cycle that produced it.
    """
    key = (key or "").strip()
    if not key or not _SAFE_KEY_RE.match(key):
        log.debug("skip snooze: unsafe/empty notification key %r", key)
        return False
    try:
        ms = int(duration_ms)
    except (TypeError, ValueError):
        log.debug("skip snooze: bad duration_ms %r", duration_ms)
        return False
    if ms <= 0:
        return False
    # Single argument to the *device* shell so it parses the single-quoted key
    # itself (the ``|`` separators would otherwise be pipes). The key is
    # validated against _SAFE_KEY_RE above, so it can't break out of the quotes.
    remote = f"cmd notification snooze --for {ms} '{key}'"
    cmd = [*_base_cmd(adb_path, serial), "shell", remote]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        log.debug("snooze: adb binary not found: %s", adb_path)
        return False
    except subprocess.TimeoutExpired:
        log.debug("snooze: adb timed out after %ss (key=%s)", timeout, key)
        return False
    if proc.returncode == 0:
        log.debug("snoozed notification key=%s for %dms", key, ms)
        return True
    log.debug(
        "snooze failed rc=%s key=%s stderr=%s",
        proc.returncode, key, proc.stderr.strip()[:200],
    )
    return False
