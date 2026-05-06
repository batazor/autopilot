"""Device screenshot via ``adb exec-out screencap -p`` (no Quartz)."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

# Homebrew (Apple Silicon) is often missing from the GUI/Streamlit PATH.
DEFAULT_ADB_BIN = "/opt/homebrew/bin/adb"


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


def adb_screencap_png(adb_bin: str = DEFAULT_ADB_BIN, serial: str | None = None) -> tuple[bytes | None, str]:
    """Return (PNG bytes, empty str) on success, or (None, error message)."""
    resolved = resolve_adb_executable(adb_bin)
    if resolved is None:
        return None, (
            "**adb** not found (Platform Tools are not on this process's PATH). "
            "Options: in the UI set a full path, e.g. "
            "`/opt/homebrew/bin/adb` (Homebrew on Apple Silicon) or "
            "`~/Library/Android/sdk/platform-tools/adb`; or set **ANDROID_HOME**; "
            "in a shell run `which adb`."
        )
    cmd: list[str] = [resolved]
    if serial and str(serial).strip():
        cmd.extend(["-s", str(serial).strip()])
    cmd.extend(["exec-out", "screencap", "-p"])
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return None, f"Failed to run {resolved!r} (FileNotFoundError)."
    if proc.returncode != 0:
        err = proc.stderr.decode(errors="replace").strip() or "unknown error"
        return None, f"ADB failed (exit {proc.returncode}): {err}"
    data = proc.stdout
    if not data.startswith(b"\x89PNG"):
        return None, (
            "ADB did not return PNG. Check `adb devices`, **bluestacks_window_title** for the serial, "
            "and USB authorization on the device."
        )
    return data, ""


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
