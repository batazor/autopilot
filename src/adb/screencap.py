"""Device screenshot via ``adb exec-out screencap -p`` (low-level ADB CLI)."""
from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from adb.frame_normalize import FrameNormalizeTransform

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


def ensure_adb_server(user_pref: str = "adb", *, timeout: float = 20.0) -> bool:
    """Best-effort: make sure a local adb server is running, return success.

    With ``network_mode: host`` the bot/api containers share the host's loopback,
    so an adb server started *inside* the container (this call, via the bundled
    ``adb`` binary) listens on ``127.0.0.1:5037`` for the whole host — the bot,
    the dashboard scan, and any emulator on loopback all reach it, and the user
    never has to install adb or run ``adb start-server`` on the host.

    ``adb start-server`` is idempotent: when a server is already up (e.g. local
    dev on macOS, or the sibling container started it first) this is a no-op.
    Failures are logged, never raised, so a missing/old adb can't block startup.
    """
    resolved = resolve_adb_executable(user_pref)
    if not resolved:
        logger.warning("ensure_adb_server: adb not found; skipping start-server")
        return False
    try:
        proc = subprocess.run(
            [resolved, "start-server"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("ensure_adb_server: 'adb start-server' failed: %s", exc)
        return False
    if proc.returncode != 0:
        logger.warning(
            "ensure_adb_server: 'adb start-server' exited %s: %s",
            proc.returncode,
            (proc.stderr or "").strip(),
        )
        return False
    logger.info("ensure_adb_server: adb server is up (%s)", resolved)
    return True


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


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# Standard PNG terminator: the IEND chunk is fixed-size and always last.
# Truncated transfers (USB hiccup, race on the adb stdout pipe) miss this byte
# sequence — libpng/cv2 then bail with "PNG input buffer is incomplete".
_PNG_IEND_TRAILER = b"\x00\x00\x00\x00IEND\xaeB`\x82"
_ADB_SCREENCAP_RETRIES = 2
_ADB_SCREENCAP_RETRY_SLEEP_S = 0.05


def _adb_screencap_raw_png_once(
    *,
    resolved: str,
    serial: str | None,
    timeout_seconds: float,
) -> tuple[bytes | None, str, bool]:
    """One ``adb exec-out screencap -p`` call. Returns ``(data, err, transient)``.

    ``transient=True`` marks failures worth retrying (truncated transfer,
    missing PNG magic) — the caller's retry loop reissues the subprocess for
    those, and surfaces persistent failures (timeout, ADB not found) directly.
    """
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
        return None, msg, False
    except FileNotFoundError:
        return None, f"Failed to run {resolved!r} (FileNotFoundError).", False
    if proc.returncode != 0:
        detail = _stderr_or_signal_detail(proc.returncode, proc.stderr)
        msg = f"ADB failed (exit {proc.returncode}): {detail}"
        logger.debug(
            "ADB screencap failed: exe=%s serial=%s detail=%s",
            resolved,
            serial,
            detail,
        )
        return None, msg, False
    data = proc.stdout
    if not data.startswith(_PNG_MAGIC):
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
        # Treat as transient — sometimes the first byte is OK but the stream is
        # garbage from a stale exec-out; a retry typically gets a clean frame.
        return None, msg, True
    if not data.endswith(_PNG_IEND_TRAILER):
        msg = (
            f"ADB screencap truncated (missing PNG IEND trailer, {len(data)}B)."
        )
        logger.debug(
            "ADB screencap truncated: exe=%s serial=%s bytes=%d tail=%r",
            resolved,
            serial,
            len(data),
            data[-16:],
        )
        return None, msg, True
    return data, "", False


def adb_screencap_raw_png(
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
    *,
    timeout_seconds: float = DEFAULT_ADB_TIMEOUT_SECONDS,
) -> tuple[bytes | None, str]:
    """Return raw device PNG bytes without letterbox normalization.

    Transient transport errors (truncated PNG, missing magic from a stale
    exec-out stream) are retried up to ``_ADB_SCREENCAP_RETRIES`` times; the
    final attempt's error message is surfaced if all retries fail.
    """
    import time as _time

    resolved = resolve_adb_executable(adb_bin)
    if resolved is None:
        return None, MSG_ADB_NOT_FOUND
    last_err = ""
    for attempt in range(_ADB_SCREENCAP_RETRIES + 1):
        data, err, transient = _adb_screencap_raw_png_once(
            resolved=resolved, serial=serial, timeout_seconds=timeout_seconds
        )
        if data is not None:
            if attempt > 0:
                logger.debug(
                    "ADB screencap recovered after %d retry(s): serial=%s",
                    attempt,
                    serial,
                )
            return data, ""
        last_err = err
        if not transient:
            return None, err
        if attempt < _ADB_SCREENCAP_RETRIES:
            _time.sleep(_ADB_SCREENCAP_RETRY_SLEEP_S)
    return None, last_err


def adb_screencap_png(
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
    *,
    timeout_seconds: float = DEFAULT_ADB_TIMEOUT_SECONDS,
    normalize: bool = True,
) -> tuple[bytes | None, str]:
    """Return PNG bytes; normalized to 720×1280 by default."""
    if not normalize:
        return adb_screencap_raw_png(
            adb_bin,
            serial,
            timeout_seconds=timeout_seconds,
        )
    img, err = adb_screencap_bgr(adb_bin, serial, normalize=True)
    if img is None:
        return None, err
    import cv2

    ok, enc = cv2.imencode(".png", img)
    if not ok:
        return None, "cv2.imencode failed after adb screencap"
    return enc.tobytes(), ""


def adb_screencap_bgr(
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
    *,
    normalize: bool = True,
) -> tuple[np.ndarray | None, str]:
    """Decode ADB PNG screencap to BGR ``numpy`` array (OpenCV convention)."""
    img, _transform, err = adb_screencap_bgr_with_transform(
        adb_bin,
        serial,
        normalize=normalize,
    )
    return img, err


def adb_screencap_bgr_with_transform(
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
    *,
    normalize: bool = True,
) -> tuple[np.ndarray | None, FrameNormalizeTransform | None, str]:
    """Decode ADB PNG screencap and include normalization geometry when available."""
    data, err = adb_screencap_raw_png(adb_bin, serial)
    if data is None:
        return None, None, err
    import cv2

    from adb.frame_normalize import normalize_adb_frame_bgr_with_transform

    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None, None, "cv2.imdecode failed (invalid PNG from adb screencap)"
    transform = None
    if normalize:
        img, transform = normalize_adb_frame_bgr_with_transform(img)
    return img, transform, ""


def adb_screencap_to_file(
    dest: Path,
    *,
    adb_bin: str = DEFAULT_ADB_BIN,
    serial: str | None = None,
    normalize: bool = True,
) -> tuple[bool, str]:
    """Write PNG to ``dest``; on success (True, path str), else (False, error)."""
    img, err = adb_screencap_bgr(adb_bin, serial, normalize=normalize)
    if img is None:
        return False, err
    import cv2

    dest.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(dest), img):
        return False, f"cv2.imwrite failed for {dest}"
    return True, str(dest)
