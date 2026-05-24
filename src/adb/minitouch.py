"""Minitouch (DeviceFarmer) input events: ~5-20 ms/tap vs ~150-300 ms for `adb shell input tap`.

Minitouch is a small native binary that writes directly to ``/dev/input/event*``
through a unix-abstract socket forwarded to a local TCP port. It is *stateful*:
the host opens a persistent connection, reads the banner (max touchscreen
coords, max pressure, max contacts), then sends text commands:

    d <contact> <x> <y> <pressure>     touch down
    m <contact> <x> <y> <pressure>     move
    u <contact>                        touch up
    c                                  commit (flush queued events)
    w <ms>                             wait (server-side, used for long-press)
    r                                  reset

Coordinate space caveat: minitouch coords are **touchscreen-native**, not
pixel-space — they range over ``[0..max_x] x [0..max_y]`` from the banner.
For an S20 FE the panel is 1080x2400 px but the digitizer reports 4095x4095.
Callers pass *device-physical* pixels; this module scales internally.

Rotation: the bot assumes portrait (720x1280 bot frame → 1080x2400 physical).
Minitouch ignores Android's rotation, so portrait-only is the safe default.
A landscape-rotated device would need axis swap — not implemented; we log a
warning if ``surface.orientation`` ≠ 0 on startup.

Public surface:
    - MinitouchStatus / get_minitouch_status(serial, adb_bin)
    - install_minitouch(serial, adb_bin)
    - MinitouchClient(serial, adb_bin, ...) — start / tap / swipe / long_press / close
"""
from __future__ import annotations

import contextlib
import logging
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)

# DeviceFarmer/minitouch repo only ships sources; prebuilts live in
# openatx/stf-binaries (which vendors DeviceFarmer's npm package).
MINITOUCH_REPO = (
    "https://raw.githubusercontent.com/openatx/stf-binaries/master"
    "/node_modules/@devicefarmer/minitouch-prebuilt/prebuilt"
)
DEVICE_TMP = "/data/local/tmp"
DEVICE_BIN = f"{DEVICE_TMP}/minitouch"
DEFAULT_PORT_BASE = 1111
_DOWNLOAD_CACHE = Path.home() / ".cache" / "wos-autopilot" / "minitouch"
_DEFAULT_PRESSURE = 50


# ---------------------------------------------------------------------------
# status + install
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class MinitouchStatus:
    serial: str
    abi: str | None = None
    sdk: str | None = None
    binary_installed: bool = False
    binary_size: int | None = None
    last_error: str | None = None

    @property
    def installed(self) -> bool:
        return self.binary_installed

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


def get_minitouch_status(serial: str, adb_bin: str) -> MinitouchStatus:
    """Probe the device for installed minitouch binary."""
    status = MinitouchStatus(serial=serial)
    try:
        status.abi = _adb_shell_text(
            ["getprop", "ro.product.cpu.abi"], serial=serial, adb_bin=adb_bin
        ) or None
        status.sdk = _adb_shell_text(
            ["getprop", "ro.build.version.sdk"], serial=serial, adb_bin=adb_bin
        ) or None
        proc = _run_adb(
            ["shell", "ls", "-l", DEVICE_BIN], serial=serial, adb_bin=adb_bin
        )
        out = proc.stdout.decode(errors="replace").strip()
        if proc.returncode == 0 and out and "No such" not in out:
            status.binary_installed = True
            parts = out.split()
            if len(parts) >= 5:
                with contextlib.suppress(ValueError):
                    status.binary_size = int(parts[4])
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


def install_minitouch(serial: str, adb_bin: str) -> MinitouchStatus:
    """Detect ABI, download matching prebuilt, push to /data/local/tmp."""
    status = get_minitouch_status(serial, adb_bin)
    if not status.abi:
        status.last_error = "could not read device ABI via getprop"
        return status

    bin_url = f"{MINITOUCH_REPO}/{status.abi}/bin/minitouch"
    bin_cache = _DOWNLOAD_CACHE / status.abi / "minitouch"
    try:
        if not bin_cache.is_file():
            logger.info("minitouch: downloading binary %s", bin_url)
            _download(bin_url, bin_cache)
        logger.info("minitouch: pushing to %s", serial)
        _run_adb(
            ["push", str(bin_cache), DEVICE_BIN],
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

    return get_minitouch_status(serial, adb_bin)


# ---------------------------------------------------------------------------
# protocol helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MinitouchBanner:
    version: int
    max_contacts: int
    max_x: int
    max_y: int
    max_pressure: int
    pid: int


def _parse_banner(text: str) -> MinitouchBanner:
    """Parse the 3-line ASCII banner minitouch prints on connect.

    Format (each line terminated with \\n):
        v <version>
        ^ <max_contacts> <max_x> <max_y> <max_pressure>
        $ <pid>
    """
    version = max_contacts = max_x = max_y = max_pressure = pid = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        head, *rest = line.split()
        if head == "v" and rest:
            version = int(rest[0])
        elif head == "^" and len(rest) >= 4:
            max_contacts, max_x, max_y, max_pressure = (int(x) for x in rest[:4])
        elif head == "$" and rest:
            pid = int(rest[0])
    if max_x <= 0 or max_y <= 0:
        msg = f"minitouch banner missing geometry: {text!r}"
        raise RuntimeError(msg)
    return MinitouchBanner(version, max_contacts, max_x, max_y, max_pressure, pid)


def _read_banner(sock: socket.socket, *, max_bytes: int = 256) -> str:
    """Drain bytes until we've seen all three banner lines (v / ^ / $)."""
    buf = b""
    sock.settimeout(5.0)
    while b"$" not in buf or buf.count(b"\n") < 3:
        chunk = sock.recv(max_bytes)
        if not chunk:
            msg = f"minitouch socket closed during banner: {buf!r}"
            raise ConnectionError(msg)
        buf += chunk
        if len(buf) > 4096:
            msg = f"minitouch banner too large: {len(buf)} bytes"
            raise RuntimeError(msg)
    sock.settimeout(None)
    return buf.decode(errors="replace")


def _detect_stderr_root_problem(stderr_text: str) -> str | None:
    """Return a friendly error if minitouch stderr indicates lack of /dev/input access."""
    lower = stderr_text.lower()
    if "permission denied" in lower and "/dev/input" in lower:
        return (
            "minitouch cannot open /dev/input/event* — device is not rooted "
            "and the adb shell user lacks input device permissions. "
            "Bot will fall back to `adb shell input` (slower)."
        )
    if "unable to find a suitable touch device" in lower:
        return (
            "minitouch did not find a usable touch device "
            "(usually a permission issue on non-rooted devices). "
            "Bot will fall back to `adb shell input` (slower)."
        )
    return None


def _parse_physical_size(text: str) -> tuple[int, int] | None:
    """`wm size` → Physical px (not Override). Minitouch uses raw panel coords."""
    for line in text.splitlines():
        line = line.strip()
        if not line.lower().startswith("physical"):
            continue
        try:
            w_str, h_str = line.split(":", 1)[1].strip().split("x")
            return int(w_str), int(h_str)
        except (ValueError, IndexError):
            continue
    return None


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


class MinitouchClient:
    """One persistent minitouch connection per ADB serial.

    Coordinates passed to ``tap``/``swipe`` are **device-physical pixels** (the
    same space AdbController already operates in — minitouch-scaling happens
    internally via the banner's ``max_x``/``max_y``).
    """

    def __init__(
        self,
        serial: str,
        adb_bin: str,
        *,
        port: int = DEFAULT_PORT_BASE,
    ) -> None:
        self.serial = serial
        self.adb_bin = adb_bin
        self.port = port
        self._proc: subprocess.Popen[bytes] | None = None
        self._sock: socket.socket | None = None
        self._banner: MinitouchBanner | None = None
        self._physical_size: tuple[int, int] | None = None
        self._lock = threading.Lock()  # serializes writes to the socket
        self._last_error: str | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Idempotent. Auto-installs binary if missing, starts server, opens socket."""
        if self._sock is not None and self._banner is not None:
            return
        status = get_minitouch_status(self.serial, self.adb_bin)
        if not status.installed:
            logger.info("minitouch: not installed on %s — installing", self.serial)
            status = install_minitouch(self.serial, self.adb_bin)
            if not status.installed:
                err = status.last_error or "install failed"
                msg = f"minitouch install failed for {self.serial}: {err}"
                raise RuntimeError(msg)

        # Read Physical size (minitouch coords are panel-native).
        wm_text = _adb_shell_text(["wm", "size"], serial=self.serial, adb_bin=self.adb_bin)
        self._physical_size = _parse_physical_size(wm_text)
        if self._physical_size is None:
            msg = f"could not read Physical size on {self.serial}: {wm_text!r}"
            raise RuntimeError(msg)

        with contextlib.suppress(Exception):
            _run_adb(
                ["shell", "pkill", "-f", "minitouch"],
                serial=self.serial, adb_bin=self.adb_bin,
            )

        cmd = [
            self.adb_bin, "-s", self.serial, "shell",
            DEVICE_BIN,
        ]
        logger.info("minitouch: starting on %s", self.serial)
        proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        # Give minitouch a moment to probe /dev/input. On non-rooted devices
        # this is when "Unable to find a suitable touch device" prints.
        time.sleep(0.8)
        if proc.poll() is not None:
            stderr = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
            friendly = _detect_stderr_root_problem(stderr)
            detail = friendly or stderr.strip() or "(no stderr)"
            self._last_error = detail
            msg = f"minitouch exited immediately: {detail}"
            raise RuntimeError(msg)
        _run_adb(
            ["forward", f"tcp:{self.port}", "localabstract:minitouch"],
            serial=self.serial, adb_bin=self.adb_bin, check=True,
        )
        time.sleep(0.2)
        self._proc = proc

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect(("127.0.0.1", self.port))
        banner_text = _read_banner(sock)
        self._banner = _parse_banner(banner_text)
        self._sock = sock
        logger.info(
            "minitouch %s ready: panel %dx%d, ts max=%dx%d, pressure max=%d",
            self.serial,
            self._physical_size[0], self._physical_size[1],
            self._banner.max_x, self._banner.max_y,
            self._banner.max_pressure,
        )

    def close(self) -> None:
        with contextlib.suppress(Exception):
            if self._sock is not None:
                self._sock.shutdown(socket.SHUT_RDWR)
        with contextlib.suppress(Exception):
            if self._sock is not None:
                self._sock.close()
        self._sock = None
        self._banner = None
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
                ["shell", "pkill", "-f", "minitouch"],
                serial=self.serial, adb_bin=self.adb_bin,
            )

    def is_alive(self) -> bool:
        return self._sock is not None and self._banner is not None

    # -- coordinate scaling ------------------------------------------------

    def _scale(self, px: int, py: int) -> tuple[int, int]:
        """Physical px → touchscreen-native coords using the banner geometry."""
        if self._banner is None or self._physical_size is None:
            msg = "minitouch not started"
            raise RuntimeError(msg)
        pw, ph = self._physical_size
        tx = int(round(px * self._banner.max_x / pw))
        ty = int(round(py * self._banner.max_y / ph))
        # Clamp to valid range (rounding can push 1px past the edge).
        tx = max(0, min(self._banner.max_x, tx))
        ty = max(0, min(self._banner.max_y, ty))
        return tx, ty

    def _pressure(self, override: int | None = None) -> int:
        if self._banner is None:
            return _DEFAULT_PRESSURE
        if override is not None:
            return max(1, min(self._banner.max_pressure, int(override)))
        # Use ~50% of max; some panels reject pressure=1 silently.
        return min(self._banner.max_pressure, _DEFAULT_PRESSURE)

    # -- write helpers -----------------------------------------------------

    def _send(self, payload: str) -> None:
        if self._sock is None:
            msg = "minitouch not started"
            raise RuntimeError(msg)
        with self._lock:
            self._sock.sendall(payload.encode("ascii"))

    # -- public input API --------------------------------------------------

    def tap(self, px: int, py: int, *, pressure: int | None = None) -> None:
        """Single-finger tap at device-physical pixel (px, py)."""
        tx, ty = self._scale(px, py)
        p = self._pressure(pressure)
        self._send(f"d 0 {tx} {ty} {p}\nc\nu 0\nc\n")

    def long_press(
        self,
        px: int,
        py: int,
        *,
        duration_ms: int = 800,
        pressure: int | None = None,
    ) -> None:
        """Press, hold for duration, release. Uses server-side ``w`` so the
        sleep happens on the device — host doesn't block on subprocess timing."""
        tx, ty = self._scale(px, py)
        p = self._pressure(pressure)
        wait = max(1, int(duration_ms))
        self._send(f"d 0 {tx} {ty} {p}\nc\nw {wait}\nu 0\nc\n")

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_ms: int = 300,
        steps: int = 16,
        pressure: int | None = None,
    ) -> None:
        """Straight swipe from (x1,y1) to (x2,y2). Coords are device-physical px."""
        tx1, ty1 = self._scale(x1, y1)
        tx2, ty2 = self._scale(x2, y2)
        p = self._pressure(pressure)
        n = max(2, int(steps))
        ms_per_step = max(1, int(duration_ms) // n)
        lines = [f"d 0 {tx1} {ty1} {p}", "c"]
        for i in range(1, n + 1):
            t = i / n
            mx = int(round(tx1 + (tx2 - tx1) * t))
            my = int(round(ty1 + (ty2 - ty1) * t))
            lines.append(f"m 0 {mx} {my} {p}")
            lines.append("c")
            lines.append(f"w {ms_per_step}")
        lines.append("u 0")
        lines.append("c")
        self._send("\n".join(lines) + "\n")

    @property
    def banner(self) -> MinitouchBanner | None:
        return self._banner

    @property
    def physical_size(self) -> tuple[int, int] | None:
        return self._physical_size

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def __enter__(self) -> MinitouchClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
