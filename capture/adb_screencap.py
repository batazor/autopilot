"""Device screenshot via ``adb exec-out screencap -p``."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path

import numpy as np

# Homebrew (Apple Silicon) is often missing from the GUI/Streamlit PATH.
DEFAULT_ADB_BIN = "/opt/homebrew/bin/adb"
DEFAULT_ADB_TIMEOUT_SECONDS = 10.0
logger = logging.getLogger(__name__)

MSG_ADB_NOT_FOUND = (
    "**adb** not found (Platform Tools are not on this process's PATH). "
    "Options: in the UI set a full path, e.g. "
    "`/opt/homebrew/bin/adb` (Homebrew on Apple Silicon) or "
    "`~/Library/Android/sdk/platform-tools/adb`; or set **ANDROID_HOME**; "
    "in a shell run `which adb`. "
    "In `config/settings.yaml` set **`worker.adb_executable`** for the embedded worker."
)


def resolve_adb_executable(user_pref: str = "adb") -> str | None:
    """
    Resolve the adb executable.

    Streamlit/Cursor often start with a reduced PATH (unlike an interactive zsh).
    Order: explicit path → ``shutil.which`` → ``ANDROID_HOME``/``ANDROID_SDK_ROOT`` →
    ``~/Library/Android/sdk/...`` → ``/opt/homebrew/bin/adb`` / ``/usr/local/bin/adb``.
    """
    pref = (user_pref or "adb").strip()
    if pref:
        expanded = Path(pref).expanduser()
        if expanded.is_file():
            return str(expanded.resolve())
    name = pref if pref and not Path(pref).is_absolute() else "adb"
    found = shutil.which(name)
    if found:
        return found
    for key in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        root = os.environ.get(key, "").strip()
        if not root:
            continue
        candidate = Path(root).expanduser() / "platform-tools" / "adb"
        if candidate.is_file():
            return str(candidate.resolve())
    mac_default = Path.home() / "Library/Android/sdk/platform-tools/adb"
    if mac_default.is_file():
        return str(mac_default.resolve())
    for common in ("/opt/homebrew/bin/adb", "/usr/local/bin/adb"):
        p = Path(common)
        if p.is_file():
            return str(p.resolve())
    return None


def _stderr_or_signal_detail(returncode: int, stderr: bytes) -> str:
    """Human-readable failure detail; negative ``returncode`` means killed by signal (-N → signal N)."""
    text = stderr.decode(errors="replace").strip()
    if returncode < 0:
        sig = -returncode
        try:
            label = signal.strsignal(sig)
        except (ValueError, OSError):
            label = f"signal {sig}"
        if text:
            return f"killed by {label} — {text}"
        return f"killed by {label}"
    return text or "unknown error"


def adb_screencap_png(
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
    *,
    timeout_seconds: float = DEFAULT_ADB_TIMEOUT_SECONDS,
) -> tuple[bytes | None, str]:
    """Return (PNG bytes, empty str) on success, or (None, error message)."""
    resolved = resolve_adb_executable(adb_bin)
    if resolved is None:
        return None, MSG_ADB_NOT_FOUND
    cmd: list[str] = [resolved]
    if serial and str(serial).strip():
        cmd.extend(["-s", str(serial).strip()])
    cmd.extend(["exec-out", "screencap", "-p"])
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=float(timeout_seconds) if timeout_seconds else None,
        )
    except subprocess.TimeoutExpired:
        msg = f"ADB screencap timed out after {timeout_seconds:.1f}s (serial={serial!r})."
        logger.debug("ADB screencap timeout: exe=%s serial=%s", resolved, serial)
        return None, msg
    except FileNotFoundError:
        return None, f"Failed to run {resolved!r} (FileNotFoundError)."
    if proc.returncode != 0:
        detail = _stderr_or_signal_detail(proc.returncode, proc.stderr)
        msg = f"ADB failed (exit {proc.returncode}): {detail}"
        logger.debug(
            "ADB screencap failed: exe=%s serial=%s detail=%s",
            resolved,
            serial,
            detail,
        )
        return None, msg
    data = proc.stdout
    if not data.startswith(b"\x89PNG"):
        msg = (
            "ADB did not return PNG. Check `adb devices` and "
            "**bluestacks_window_title** (serial); verify USB authorization."
        )
        logger.debug(
            "ADB screencap bad output: exe=%s serial=%s bytes=%d",
            resolved,
            serial,
            len(data or b""),
        )
        return None, msg
    return data, ""


def adb_screencap_bgr(
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
) -> tuple[np.ndarray | None, str]:
    """Decode ADB PNG screencap to BGR ``numpy`` array (OpenCV convention)."""
    data, err = adb_screencap_png(adb_bin, serial)
    if data is None:
        return None, err
    import cv2

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None, "cv2.imdecode failed (invalid PNG from adb screencap)"
    return img, ""


def adb_screencap_to_file(
    dest: Path,
    *,
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
) -> tuple[bool, str]:
    """Write PNG to ``dest``; on success (True, path str), else (False, error)."""
    data, err = adb_screencap_png(adb_bin, serial)
    if data is None:
        return False, err
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True, str(dest)
