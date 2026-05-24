"""Minicap (DeviceFarmer) screen capture: ~15-40 ms/frame vs ~400 ms for `adb screencap`.

Minicap streams JPEG frames over a unix-abstract socket forwarded to a local TCP
port. It is *push*-based — frames arrive only when the device framebuffer
changes — which is desirable (no wasted USB/CPU on static screens) but means
callers may need to fall back to the last cached frame on a `capture()` timeout.

Public surface:
    - MinicapStatus / get_minicap_status(serial, adb_bin)
    - install_minicap(serial, adb_bin)
    - MinicapClient(serial, adb_bin, ...) — start / capture / close
"""
from __future__ import annotations

import contextlib
import logging
import socket
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

MINICAP_REPO = "https://raw.githubusercontent.com/DeviceFarmer/minicap/master"
DEVICE_TMP = "/data/local/tmp"
DEVICE_BIN = f"{DEVICE_TMP}/minicap"
DEVICE_LIB = f"{DEVICE_TMP}/minicap.so"
DEFAULT_TARGET_SIZE = (720, 1280)
DEFAULT_PORT_BASE = 1313
_DOWNLOAD_CACHE = Path.home() / ".cache" / "wos-autopilot" / "minicap"


# ---------------------------------------------------------------------------
# status + install
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MinicapStatus:
    serial: str
    abi: str | None = None
    sdk: str | None = None
    binary_installed: bool = False
    library_installed: bool = False
    binary_size: int | None = None
    library_size: int | None = None
    last_error: str | None = None

    @property
    def installed(self) -> bool:
        return self.binary_installed and self.library_installed

    def to_dict(self) -> dict[str, object]:
        out = asdict(self)
        out["installed"] = self.installed
        return out


def _run_adb(
    args: Iterable[str],
    *,
    serial: str,
    adb_bin: str,
    timeout: float = 10.0,
    check: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    cmd = [adb_bin, "-s", serial, *args]
    return subprocess.run(cmd, capture_output=True, timeout=timeout, check=check)


def _adb_shell_text(args: Iterable[str], *, serial: str, adb_bin: str) -> str:
    proc = _run_adb(["shell", *args], serial=serial, adb_bin=adb_bin)
    return proc.stdout.decode(errors="replace").strip()


def get_minicap_status(serial: str, adb_bin: str) -> MinicapStatus:
    """Probe the device for installed minicap binary + library."""
    status = MinicapStatus(serial=serial)
    try:
        status.abi = _adb_shell_text(
            ["getprop", "ro.product.cpu.abi"], serial=serial, adb_bin=adb_bin
        ) or None
        status.sdk = _adb_shell_text(
            ["getprop", "ro.build.version.sdk"], serial=serial, adb_bin=adb_bin
        ) or None
        # `ls -l <path>` prints size in 5th column when the file exists.
        for path, set_present, set_size in (
            (
                DEVICE_BIN,
                lambda v: setattr(status, "binary_installed", v),
                lambda v: setattr(status, "binary_size", v),
            ),
            (
                DEVICE_LIB,
                lambda v: setattr(status, "library_installed", v),
                lambda v: setattr(status, "library_size", v),
            ),
        ):
            proc = _run_adb(
                ["shell", "ls", "-l", path], serial=serial, adb_bin=adb_bin
            )
            out = proc.stdout.decode(errors="replace").strip()
            if proc.returncode != 0 or "No such" in out or not out:
                set_present(False)
                continue
            set_present(True)
            parts = out.split()
            if len(parts) >= 5:
                with contextlib.suppress(ValueError):
                    set_size(int(parts[4]))
    except subprocess.TimeoutExpired as exc:
        status.last_error = f"adb timed out: {exc}"
    except Exception as exc:
        status.last_error = str(exc)
    return status


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            urllib.request.urlopen(url, timeout=30) as resp,
            dest.open("wb") as fp,
        ):
            fp.write(resp.read())
    except urllib.error.HTTPError as exc:
        msg = f"download failed ({exc.code}): {url}"
        raise RuntimeError(msg) from exc


def install_minicap(serial: str, adb_bin: str) -> MinicapStatus:
    """Detect ABI/SDK, download matching prebuilts, push to /data/local/tmp."""
    status = get_minicap_status(serial, adb_bin)
    if not status.abi or not status.sdk:
        status.last_error = "could not read device ABI / SDK via getprop"
        return status

    bin_url = f"{MINICAP_REPO}/libs/{status.abi}/minicap"
    lib_url = (
        f"{MINICAP_REPO}/jni/minicap-shared/aosp/libs/"
        f"android-{status.sdk}/{status.abi}/minicap.so"
    )
    bin_cache = _DOWNLOAD_CACHE / status.abi / "minicap"
    lib_cache = _DOWNLOAD_CACHE / status.abi / f"android-{status.sdk}-minicap.so"
    try:
        if not bin_cache.is_file():
            logger.info("minicap: downloading binary %s", bin_url)
            _download(bin_url, bin_cache)
        if not lib_cache.is_file():
            logger.info("minicap: downloading library %s", lib_url)
            _download(lib_url, lib_cache)
        logger.info("minicap: pushing to %s", serial)
        _run_adb(
            ["push", str(bin_cache), DEVICE_BIN],
            serial=serial, adb_bin=adb_bin, check=True, timeout=30,
        )
        _run_adb(
            ["push", str(lib_cache), DEVICE_LIB],
            serial=serial, adb_bin=adb_bin, check=True, timeout=30,
        )
        _run_adb(
            ["shell", "chmod", "755", DEVICE_BIN],
            serial=serial, adb_bin=adb_bin, check=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode(errors="replace").strip()
        status.last_error = f"{exc.cmd}: {stderr or exc}"
        return status
    except Exception as exc:
        status.last_error = str(exc)
        return status

    return get_minicap_status(serial, adb_bin)


# ---------------------------------------------------------------------------
# capture client
# ---------------------------------------------------------------------------


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            msg = f"minicap socket closed (wanted {n}, got {len(buf)})"
            raise ConnectionError(msg)
        buf.extend(chunk)
    return bytes(buf)


def _parse_wm_size(text: str) -> tuple[int, int] | None:
    """Prefer `Override size: …` (set by the bot's `wm size 720x1280`), fallback to Physical."""
    physical = override = None
    for line in text.splitlines():
        line = line.strip()
        try:
            label, value = line.split(":", 1)
            w_str, h_str = value.strip().split("x")
            sz = (int(w_str), int(h_str))
        except (ValueError, IndexError):
            continue
        if label.lower().startswith("override"):
            override = sz
        elif label.lower().startswith("physical"):
            physical = sz
    return override or physical


@dataclass(slots=True)
class _CachedFrame:
    image: np.ndarray
    captured_at: float


class MinicapClient:
    """One persistent minicap connection per ADB serial.

    Push-model handling: ``capture(timeout_s)`` returns the most recent frame.
    If a new frame arrives within ``timeout_s`` it's returned; otherwise the
    last cached frame is returned with no error (the bot is happy with a stable
    framebuffer when nothing on screen has moved).
    """

    def __init__(
        self,
        serial: str,
        adb_bin: str,
        *,
        port: int = DEFAULT_PORT_BASE,
        target_size: tuple[int, int] = DEFAULT_TARGET_SIZE,
    ) -> None:
        self.serial = serial
        self.adb_bin = adb_bin
        self.port = port
        self.target_size = target_size
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._cache: _CachedFrame | None = None
        self._lock = threading.Lock()
        self._reader_thread: threading.Thread | None = None
        self._frame_event = threading.Event()
        self._stop = threading.Event()
        self._last_error: str | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Idempotent. Auto-install if missing, start server, connect socket, spawn reader."""
        if self._sock is not None and self._reader_thread and self._reader_thread.is_alive():
            return
        status = get_minicap_status(self.serial, self.adb_bin)
        if not status.installed:
            logger.info("minicap: not installed on %s — installing", self.serial)
            status = install_minicap(self.serial, self.adb_bin)
            if not status.installed:
                err = status.last_error or "install failed"
                msg = f"minicap install failed for {self.serial}: {err}"
                raise RuntimeError(msg)

        # Kill any stale instance on the device.
        with contextlib.suppress(Exception):
            _run_adb(
                ["shell", "pkill", "-f", "minicap"],
                serial=self.serial, adb_bin=self.adb_bin,
            )

        wm_text = _adb_shell_text(["wm", "size"], serial=self.serial, adb_bin=self.adb_bin)
        real = _parse_wm_size(wm_text) or self.target_size
        vw, vh = self.target_size
        p_arg = f"{real[0]}x{real[1]}@{vw}x{vh}/0"
        cmd = [
            self.adb_bin, "-s", self.serial, "shell",
            f"LD_LIBRARY_PATH={DEVICE_TMP}", DEVICE_BIN, "-P", p_arg,
        ]
        logger.info("minicap: starting (%s) on %s", p_arg, self.serial)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        time.sleep(0.6)
        _run_adb(
            ["forward", f"tcp:{self.port}", "localabstract:minicap"],
            serial=self.serial, adb_bin=self.adb_bin, check=True,
        )
        time.sleep(0.2)
        if proc.poll() is not None:
            stderr = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
            msg = f"minicap exited immediately: {stderr.strip() or '(no stderr)'}"
            raise RuntimeError(msg)
        self._proc = proc

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        sock.connect(("127.0.0.1", self.port))
        # banner: first byte is version, second is its total length.
        head = _recv_exact(sock, 2)
        banner_size = head[1]
        if banner_size > 2:
            _recv_exact(sock, banner_size - 2)
        sock.settimeout(None)
        self._sock = sock

        self._stop.clear()
        self._frame_event.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop, name=f"minicap-{self.serial}", daemon=True,
        )
        self._reader_thread.start()

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(Exception):
            if self._sock is not None:
                self._sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(Exception):
            if self._sock is not None:
                self._sock.close()
        self._sock = None
        with contextlib.suppress(Exception):
            if self._proc is not None:
                self._proc.terminate()
                self._proc.wait(timeout=2)
        self._proc = None
        with contextlib.suppress(Exception):
            _run_adb(
                ["forward", "--remove", f"tcp:{self.port}"],
                serial=self.serial, adb_bin=self.adb_bin,
            )
        with contextlib.suppress(Exception):
            _run_adb(
                ["shell", "pkill", "-f", "minicap"],
                serial=self.serial, adb_bin=self.adb_bin,
            )

    # -- capture ------------------------------------------------------------

    def _read_loop(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                size_bytes = _recv_exact(sock, 4)
                size = struct.unpack("<I", size_bytes)[0]
                jpeg = _recv_exact(sock, size)
            except (ConnectionError, OSError) as exc:
                if not self._stop.is_set():
                    self._last_error = f"socket read failed: {exc}"
                    logger.warning("minicap %s: %s", self.serial, self._last_error)
                return
            arr = np.frombuffer(jpeg, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                continue
            with self._lock:
                self._cache = _CachedFrame(image=img, captured_at=time.monotonic())
            self._frame_event.set()

    def capture(self, timeout_s: float = 0.5) -> tuple[np.ndarray | None, str]:
        """Return (BGR frame, error). Cached frame returned if no new one within timeout."""
        if self._sock is None or self._reader_thread is None or not self._reader_thread.is_alive():
            return None, self._last_error or "minicap not started"

        self._frame_event.clear()
        if not self._frame_event.wait(timeout=timeout_s):
            with self._lock:
                cached = self._cache
            if cached is not None:
                return cached.image, ""
            return None, "no frame received yet"

        with self._lock:
            cached = self._cache
        if cached is None:
            return None, "frame event fired but cache empty"
        return cached.image, ""

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def is_alive(self) -> bool:
        return (
            self._reader_thread is not None
            and self._reader_thread.is_alive()
            and self._sock is not None
        )

    # context-manager sugar
    def __enter__(self) -> MinicapClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
