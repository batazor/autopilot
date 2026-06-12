"""scrcpy (Genymobile) screen capture + input via a single shared server process.

scrcpy ships a Java/DEX server pushed to ``/data/local/tmp/scrcpy-server.jar``
and launched through ``app_process``. It opens a forwarded local abstract
socket on which the host opens up to three connections, in this fixed order:

    1. video socket  (when video=true)
    2. audio socket  (when audio=true) — we leave it off
    3. control socket (when control=true)

The video socket starts with codec id + session metadata, then streams raw
H.264 NAL units with a 12-byte per-packet header. In scrcpy v4 the top bit
marks session packets (resolution updates, no payload); media packets use the
next two PTS bits as config / key-frame flags. The control socket is
bidirectional binary; each touch event is a 32-byte message in the format
Server.java decodes in ``ControlMessageReader``.

This module covers both ends so a single ``scrcpy-server`` process powers both
:class:`adb.bot_actions.BotActions` screenshots and :class:`adb.controller.AdbController`
input — there is one TCP forward and one device-side process per ADB serial.

Public surface:
    - ScrcpyStatus / get_scrcpy_status(serial, adb_bin)
    - install_scrcpy(serial, adb_bin)
    - ScrcpyClient(serial, adb_bin, ...) — start / read_latest_frame_bgr / tap / swipe / long_press / close
    - get_or_create_scrcpy_client(serial, adb_bin, port) — process-wide registry
"""
from __future__ import annotations

import contextlib
import logging
import os
import random
import secrets
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

from adb.controller_types import _gauss_between

if TYPE_CHECKING:
    from collections.abc import Iterable

    import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

SCRCPY_SERVER_VERSION = "4.0"
_SERVER_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/v{SCRCPY_SERVER_VERSION}"
    f"/scrcpy-server-v{SCRCPY_SERVER_VERSION}"
)
DEVICE_TMP = "/data/local/tmp"
DEVICE_JAR = f"{DEVICE_TMP}/scrcpy-server.jar"
DEFAULT_PORT_BASE = 1515
_DOWNLOAD_CACHE = Path.home() / ".cache" / "wos-autopilot" / "scrcpy"
# v4.0 server artifact is ~715 KiB. Older v3.x server jars were ~90 KiB and
# can still be left on /data/local/tmp from previous runs; treating those as
# valid makes the client launch an incompatible server.
_MIN_SERVER_JAR_SIZE = 700_000

# Frame meta flags (scrcpy v4). The high bit marks a session packet (video
# size/update, no payload).
_PACKET_FLAG_SESSION = 1 << 63

# Control message types.
_CTRL_INJECT_TOUCH_EVENT = 2

# AMotionEvent actions.
_ACTION_DOWN = 0
_ACTION_UP = 1
_ACTION_MOVE = 2

# AMotionEvent buttons.
_BUTTON_PRIMARY = 1

# Touch pressure (uint16, 0xFFFF = max press, 0 = released).
_PRESSURE_DOWN = 0xFFFF
_PRESSURE_UP = 0
_PRESSURE_MOVE_MIN = 0xE000
_TAP_HOLD_MS_MIN = 35
_TAP_HOLD_MS_MAX = 90
_TAP_MICRO_MOVE_PROBABILITY = 0.28
_LONG_SWIPE_OVERSHOOT_MIN_PX = 260

# Server send_dummy_byte=true sends one 0x00 byte on the first connection
# (the video socket) so the host knows ``adb forward`` actually reached the
# device-side socket (forward succeeds even before the server binds).
_DUMMY_BYTE = b"\x00"


def _adb_forward_host() -> str:
    """Return the host where ``adb forward tcp:<port>`` listens.

    ``adb forward`` opens the TCP listener in the adb *server* network
    namespace. In local runs that is this process and 127.0.0.1 is correct.
    In Docker Desktop deployments we often talk to the host adb server via
    ``ADB_SERVER_SOCKET=tcp:host.docker.internal:5037``, so the forwarded port
    is reachable on ``host.docker.internal`` rather than container loopback.
    """
    explicit = os.environ.get("WOS_ADB_FORWARD_HOST", "").strip()
    if explicit:
        return explicit
    server_socket = os.environ.get("ADB_SERVER_SOCKET", "").strip()
    if not server_socket.startswith("tcp:"):
        return "127.0.0.1"
    target = server_socket[4:]
    if target.startswith("["):
        host, sep, _rest = target[1:].partition("]:")
        return host if sep else "127.0.0.1"
    host, sep, _port = target.rpartition(":")
    return host if sep and host else "127.0.0.1"


# ---------------------------------------------------------------------------
# status + install
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ScrcpyStatus:
    serial: str
    abi: str | None = None
    sdk: str | None = None
    jar_installed: bool = False
    jar_size: int | None = None
    last_error: str | None = None

    @property
    def installed(self) -> bool:
        return self.jar_installed

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


def _parse_wc_size(out: str) -> int | None:
    first = out.strip().split(maxsplit=1)[0] if out.strip() else ""
    with contextlib.suppress(ValueError):
        return int(first)
    return None


def get_scrcpy_status(serial: str, adb_bin: str) -> ScrcpyStatus:
    """Probe the device for installed scrcpy-server jar."""
    status = ScrcpyStatus(serial=serial)
    try:
        status.abi = _adb_shell_text(
            ["getprop", "ro.product.cpu.abi"], serial=serial, adb_bin=adb_bin
        ) or None
        status.sdk = _adb_shell_text(
            ["getprop", "ro.build.version.sdk"], serial=serial, adb_bin=adb_bin
        ) or None
        proc = _run_adb(
            ["shell", "ls", "-l", DEVICE_JAR], serial=serial, adb_bin=adb_bin
        )
        out = proc.stdout.decode(errors="replace").strip()
        if proc.returncode == 0 and out and "No such" not in out:
            status.jar_installed = True
            # `ls -l` format varies across Android builds/toybox versions.
            # `wc -c` gives a stable byte count, which prevents false
            # "outdated jar" detections and repeated pushes on every start.
            size_proc = _run_adb(
                ["shell", "wc", "-c", DEVICE_JAR], serial=serial, adb_bin=adb_bin
            )
            if size_proc.returncode == 0:
                status.jar_size = _parse_wc_size(
                    size_proc.stdout.decode(errors="replace")
                )
            parts = out.split()
            if status.jar_size is None and len(parts) >= 5:
                with contextlib.suppress(ValueError):
                    status.jar_size = int(parts[4])
    except subprocess.TimeoutExpired as exc:
        status.last_error = f"adb timed out: {exc}"
    except Exception as exc:
        status.last_error = str(exc)
    return status


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (
            urllib.request.urlopen(url, timeout=60) as resp,
            dest.open("wb") as fp,
        ):
            fp.write(resp.read())
    except urllib.error.HTTPError as exc:
        msg = f"download failed ({exc.code}): {url}"
        raise RuntimeError(msg) from exc


def install_scrcpy(serial: str, adb_bin: str) -> ScrcpyStatus:
    """Download scrcpy-server jar (cached locally) and push to /data/local/tmp."""
    status = get_scrcpy_status(serial, adb_bin)
    jar_cache = _DOWNLOAD_CACHE / f"scrcpy-server-v{SCRCPY_SERVER_VERSION}.jar"
    try:
        if not jar_cache.is_file():
            logger.info("scrcpy: downloading server jar %s", _SERVER_URL)
            _download(_SERVER_URL, jar_cache)
        logger.info("scrcpy: pushing jar to %s", serial)
        _run_adb(
            ["push", str(jar_cache), DEVICE_JAR],
            serial=serial, adb_bin=adb_bin, check=True, timeout=30,
        )
    except subprocess.CalledProcessError as exc:
        stdout = (exc.stdout or b"").decode(errors="replace").strip()
        stderr = (exc.stderr or b"").decode(errors="replace").strip()
        detail = stderr or stdout or str(exc)
        status.last_error = f"{exc.cmd} failed with exit {exc.returncode}: {detail}"
        return status
    except Exception as exc:
        status.last_error = str(exc)
        return status
    return get_scrcpy_status(serial, adb_bin)


def _server_status_current(status: ScrcpyStatus) -> bool:
    """Return True iff the installed server is plausible for this client."""
    return bool(
        status.installed
        and status.jar_size is not None
        and status.jar_size >= _MIN_SERVER_JAR_SIZE
    )


# ---------------------------------------------------------------------------
# socket helpers
# ---------------------------------------------------------------------------


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            msg = f"scrcpy socket closed after {len(buf)}/{n} bytes"
            raise ConnectionError(msg)
        buf.extend(chunk)
    return bytes(buf)


def _human_swipe_points(
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    *,
    steps: int,
) -> list[tuple[int, int]]:
    """Return MOVE points for a slightly curved, eased finger trail.

    The returned list excludes the DOWN point and includes the final endpoint,
    so callers can emit DOWN once, MOVE for every returned point, then UP.
    """
    n = max(2, int(steps))
    dx = float(x2 - x1)
    dy = float(y2 - y1)
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0.0:
        return [(int(x2), int(y2)) for _ in range(n)]

    perp_x = -dy / length
    perp_y = dx / length
    offset = _gauss_between(0.015, 0.055) * length * random.choice((-1.0, 1.0))
    cx = (x1 + x2) / 2.0 + perp_x * offset
    cy = (y1 + y2) / 2.0 + perp_y * offset
    out: list[tuple[int, int]] = []
    overshoot: tuple[float, float] | None = None
    if length >= _LONG_SWIPE_OVERSHOOT_MIN_PX and random.random() < 0.35:
        overshoot_px = min(42.0, max(8.0, length * _gauss_between(0.025, 0.07)))
        overshoot = (x2 + dx / length * overshoot_px, y2 + dy / length * overshoot_px)
    for i in range(1, n + 1):
        linear_t = i / n
        # Smoothstep: slower at the start/end, faster in the middle.
        t = linear_t * linear_t * (3.0 - 2.0 * linear_t)
        target_x = x2
        target_y = y2
        if overshoot is not None and i >= n - 1:
            target_x, target_y = overshoot
        bx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * target_x
        by = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * target_y
        if i < n:
            # Gaussian finger tremor on intermediate points (σ = 0.75 px).
            bx += _gauss_between(-1.5, 1.5)
            by += _gauss_between(-1.5, 1.5)
        out.append((int(round(bx)), int(round(by))))
    if overshoot is not None:
        settle_steps = random.randint(1, 2)
        for i in range(settle_steps):
            t = (i + 1) / settle_steps
            sx = overshoot[0] + (x2 - overshoot[0]) * t
            sy = overshoot[1] + (y2 - overshoot[1]) * t
            out.append((int(round(sx)), int(round(sy))))
    if out:
        out[-1] = (int(x2), int(y2))
    return out


def _human_step_sleeps(duration_ms: int, *, steps: int) -> list[float]:
    """Distribute ``duration_ms`` over ``steps`` uneven intervals."""
    n = max(1, int(steps))
    total_s = max(0.001, duration_ms / 1000.0)
    weights = [_gauss_between(0.75, 1.25) for _ in range(n)]
    total_weight = sum(weights) or 1.0
    return [max(0.001, total_s * weight / total_weight) for weight in weights]


# ---------------------------------------------------------------------------
# H.264 decoder
# ---------------------------------------------------------------------------


class _H264Decoder:
    """Thin wrapper around PyAV's stateful H.264 codec context.

    PyAV is imported lazily so the module loads on hosts without ``av``
    installed — only ``ScrcpyClient.start()`` requires the runtime dep.
    """

    def __init__(self) -> None:
        try:
            import av
        except ImportError as exc:
            msg = (
                "scrcpy screenshot backend requires PyAV. "
                "Install with: `uv add av` (or pin >=12.0 in pyproject.toml)."
            )
            raise RuntimeError(msg) from exc
        self._av = av
        self._ctx = av.CodecContext.create("h264", "r")

    def decode(self, packet_bytes: bytes) -> list[np.ndarray]:
        """Push one packet of raw NAL units, return any frames it produced.

        Config-only packets (SPS/PPS) typically produce zero frames; key + delta
        frames produce one each. Invalid-data errors are swallowed — they
        usually mean the decoder is still waiting for SPS/PPS at startup.
        """
        try:
            packet = self._av.Packet(packet_bytes)
            frames = self._ctx.decode(packet)
        except self._av.error.InvalidDataError:
            return []
        except Exception:
            logger.debug("scrcpy: h264 decode raised", exc_info=True)
            return []
        out: list[np.ndarray] = []
        for frame in frames:
            try:
                out.append(frame.to_ndarray(format="bgr24"))
            except Exception:
                logger.debug("scrcpy: frame.to_ndarray failed", exc_info=True)
        return out


# ---------------------------------------------------------------------------
# client
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _CachedFrame:
    image: np.ndarray
    captured_at: float


class ScrcpyClient:
    """Single ``scrcpy-server`` process per ADB serial; one video socket + one control socket.

    Coordinates passed to ``tap``/``swipe`` are **device-physical pixels** in
    the resolution scrcpy reports via its codec-meta header (read from the
    video socket banner). The host-side bot frame is already in this space
    after :class:`adb.frame_normalize` runs.
    """

    def __init__(
        self,
        serial: str,
        adb_bin: str,
        *,
        port: int = DEFAULT_PORT_BASE,
        max_size: int = 0,
        max_fps: int = 0,
        video_bit_rate: int = 2_000_000,
    ) -> None:
        self.serial = serial
        self.adb_bin = adb_bin
        self.port = port
        self.max_size = max(0, int(max_size))
        self.max_fps = max(0, int(max_fps))
        self.video_bit_rate = max(100_000, int(video_bit_rate))
        self._scid = f"{secrets.randbits(31):08x}"
        self._abstract_name = f"scrcpy_{self._scid}"
        self._proc: subprocess.Popen[bytes] | None = None
        self._video_sock: socket.socket | None = None
        self._control_sock: socket.socket | None = None
        self._start_lock = threading.Lock()
        self._control_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._frame_event = threading.Event()
        self._cache: _CachedFrame | None = None
        self._cache_lock = threading.Lock()
        self._device_name: str = ""
        self._codec_size: tuple[int, int] | None = None
        self._last_error: str | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Idempotent. Auto-install jar if missing, launch server, open sockets, start decoder thread."""
        with self._start_lock:
            self._start_locked()

    def _start_locked(self) -> None:
        if self.is_alive():
            return

        status = get_scrcpy_status(self.serial, self.adb_bin)
        if not _server_status_current(status):
            logger.info(
                "scrcpy: server jar missing/outdated on %s (size=%s) — installing v%s",
                self.serial,
                status.jar_size,
                SCRCPY_SERVER_VERSION,
            )
            status = install_scrcpy(self.serial, self.adb_bin)
            if not _server_status_current(status):
                err = status.last_error or "install failed"
                msg = f"scrcpy install failed for {self.serial}: {err}"
                raise RuntimeError(msg)

        # Reap any stale server process from a prior run.
        with contextlib.suppress(Exception):
            _run_adb(
                ["shell", "pkill", "-f", "com.genymobile.scrcpy.Server"],
                serial=self.serial, adb_bin=self.adb_bin,
            )

        # Tunnel forward: host connects to forwarded TCP port; server binds the
        # device-side abstract socket and accepts. The adb forward must exist
        # before the server starts; otherwise recent scrcpy-server builds can
        # terminate while waiting for their socket.
        with contextlib.suppress(Exception):
            _run_adb(
                ["forward", "--remove", f"tcp:{self.port}"],
                serial=self.serial,
                adb_bin=self.adb_bin,
            )
        _run_adb(
            ["forward", f"tcp:{self.port}", f"localabstract:{self._abstract_name}"],
            serial=self.serial, adb_bin=self.adb_bin, check=True,
        )

        # Order on the device side determines socket roles (video → control
        # here, audio off).
        server_args = [
            f"scid={self._scid}",
            "log_level=warn",
            "audio=false",
            "tunnel_forward=true",
            "keep_active=true",
            "cleanup=false",
            # Do not pass v3 tuning/default options (video_bit_rate, max_fps,
            # max_size, raw_stream, etc.) or even default-valued booleans
            # (video/control/send_*). On at least Samsung SM-G780G/Android 13,
            # explicitly passing several otherwise-valid options makes the
            # server abort or close the socket before the initial session
            # packet. The official v4 client also relies on defaults and only
            # passes only a small option set for this no-audio forward tunnel.
        ]
        cmd = [
            self.adb_bin, "-s", self.serial, "shell",
            f"CLASSPATH={DEVICE_JAR}",
            "app_process", "/", "com.genymobile.scrcpy.Server",
            SCRCPY_SERVER_VERSION,
            *server_args,
        ]
        logger.info(
            "scrcpy: starting server v%s on %s (scid=%s, port=%d)",
            SCRCPY_SERVER_VERSION, self.serial, self._scid, self.port,
        )
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self._proc = proc

        try:
            self._connect_sockets()
        except Exception as exc:
            detail = ""
            if proc.poll() is not None:
                stderr = (
                    proc.stderr.read() if proc.stderr else b""
                ).decode(errors="replace")
                self._last_error = stderr.strip() or "(no stderr)"
                msg = f"scrcpy server exited immediately: {self._last_error}"
                self.close()
                raise RuntimeError(msg) from None
            with contextlib.suppress(Exception):
                proc.terminate()
                _stdout, stderr_b = proc.communicate(timeout=1.0)
                detail = stderr_b.decode(errors="replace").strip()
            self.close()
            if detail:
                msg = f"{exc}; server stderr: {detail}"
                raise RuntimeError(msg) from exc
            raise

        self._stop.clear()
        self._frame_event.clear()
        self._reader_thread = threading.Thread(
            target=self._read_loop, name=f"scrcpy-{self.serial}", daemon=True,
        )
        self._reader_thread.start()
        logger.info(
            "scrcpy %s ready: device=%r codec=h264 res=%s",
            self.serial, self._device_name, self._codec_size,
        )

    def _connect_sockets(self) -> None:
        """Open video then control connections to the forwarded port.

        ``tunnel_forward=true`` makes the server accept connections in the
        order video → audio → control (we skip audio). The first byte on the
        first socket is the ``send_dummy_byte`` synchronisation marker.

        scrcpy-server v4 sends device/codec/session meta only after all
        expected sockets are connected, so connect control after the dummy
        byte and before reading the video metadata.
        """
        forward_host = _adb_forward_host()

        # Video socket — first accepted connection. `adb forward` may accept
        # the host TCP connection before the device-side LocalServerSocket is
        # actually listening, then immediately close it. Retry until the server
        # sends the dummy byte, which proves that this connection reached the
        # real scrcpy server.
        video: socket.socket | None = None
        for attempt in range(50):
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            candidate.settimeout(3.0)
            try:
                candidate.connect((forward_host, self.port))
                dummy = _recv_exact(candidate, 1)
                if dummy == _DUMMY_BYTE:
                    video = candidate
                    break
                msg = f"scrcpy: unexpected first byte on video socket: {dummy!r}"
                raise RuntimeError(msg)
            except (ConnectionRefusedError, OSError):
                with contextlib.suppress(Exception):
                    candidate.close()
                if attempt == 49:
                    raise
                time.sleep(0.1)
        if video is None:
            msg = "scrcpy: video socket did not reach server"
            raise RuntimeError(msg)

        # Control socket — same forwarded port, second accept.
        control = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        control.settimeout(5.0)
        control.connect((forward_host, self.port))
        control.settimeout(None)
        self._control_sock = control

        device_name_raw = _recv_exact(video, 64)
        self._device_name = device_name_raw.split(b"\x00", 1)[0].decode(
            "utf-8", errors="replace"
        )
        # v4 stream prelude:
        #   codec id: 4 bytes ("h264")
        #   initial session packet: 12 bytes, high bit set, width/height in
        #   bytes 4..12. Older v2/v3 sent codec+size as one 12-byte block.
        codec_id = _recv_exact(video, 4)
        if codec_id != b"h264":
            msg = f"scrcpy: unexpected video codec id: {codec_id!r}"
            raise RuntimeError(msg)
        session = _recv_exact(video, 12)
        session_flags = struct.unpack(">Q", session[0:8])[0]
        if not (session_flags & _PACKET_FLAG_SESSION):
            msg = f"scrcpy: expected initial session packet, got {session.hex()}"
            raise RuntimeError(msg)
        width, height = struct.unpack(">II", session[4:12])
        self._codec_size = (int(width), int(height))
        video.settimeout(None)
        self._video_sock = video

    def close(self) -> None:
        self._stop.set()
        for sock in (self._video_sock, self._control_sock):
            with contextlib.suppress(Exception):
                if sock is not None:
                    sock.shutdown(socket.SHUT_RDWR)
            with contextlib.suppress(Exception):
                if sock is not None:
                    sock.close()
        self._video_sock = None
        self._control_sock = None
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
                ["shell", "pkill", "-f", "com.genymobile.scrcpy.Server"],
                serial=self.serial, adb_bin=self.adb_bin,
            )

    def is_alive(self) -> bool:
        return (
            self._video_sock is not None
            and self._control_sock is not None
            and self._reader_thread is not None
            and self._reader_thread.is_alive()
        )

    # -- video --------------------------------------------------------------

    def _read_loop(self) -> None:
        sock = self._video_sock
        if sock is None:
            return
        decoder: _H264Decoder | None = None
        while not self._stop.is_set():
            try:
                header = _recv_exact(sock, 12)
                pts_flags = struct.unpack(">Q", header[0:8])[0]
                if pts_flags & _PACKET_FLAG_SESSION:
                    width, height = struct.unpack(">II", header[4:12])
                    self._codec_size = (int(width), int(height))
                    continue
                size = struct.unpack(">I", header[8:12])[0]
                if size == 0:
                    continue
                payload = _recv_exact(sock, size)
            except (ConnectionError, OSError) as exc:
                if not self._stop.is_set():
                    self._last_error = f"video socket read failed: {exc}"
                    logger.warning("scrcpy %s: %s", self.serial, self._last_error)
                return
            if decoder is None:
                decoder = _H264Decoder()
            frames = decoder.decode(payload)
            if not frames:
                continue
            # Use the most recent frame (skip older ones in a multi-frame batch).
            img = frames[-1]
            with self._cache_lock:
                self._cache = _CachedFrame(image=img, captured_at=time.monotonic())
            self._frame_event.set()

    def read_latest_frame_bgr(
        self,
        timeout_s: float = 0.5,
        *,
        not_before_s: float | None = None,
    ) -> tuple[np.ndarray | None, str]:
        """Return (BGR frame, error).

        Without ``not_before_s`` this returns the cached frame if no new one
        arrives before ``timeout_s``. With ``not_before_s`` it waits for a frame
        decoded at or after that monotonic timestamp; stale cached frames are
        intentionally rejected so post-tap analyzers do not re-read the screen
        that existed before the touch was processed.
        """
        if not self.is_alive():
            return None, self._last_error or "scrcpy not started"
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            with self._cache_lock:
                cached = self._cache
            if cached is not None and (
                not_before_s is None or cached.captured_at >= not_before_s
            ):
                return cached.image, ""
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if cached is None:
                    return None, "no frame received yet"
                if not_before_s is not None:
                    return None, "no frame received after post-action boundary"
                return cached.image, ""
            self._frame_event.clear()
            with self._cache_lock:
                cached = self._cache
            if cached is not None and (
                not_before_s is None or cached.captured_at >= not_before_s
            ):
                return cached.image, ""
            if not self._frame_event.wait(timeout=remaining):
                continue

    @property
    def codec_size(self) -> tuple[int, int] | None:
        return self._codec_size

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def device_name(self) -> str:
        return self._device_name

    # -- control ------------------------------------------------------------

    def _send_touch(
        self,
        action: int,
        x: int,
        y: int,
        *,
        pressure: int,
        buttons: int,
        pointer_id: int = 0,
    ) -> None:
        if self._control_sock is None or self._codec_size is None:
            msg = "scrcpy control socket not started"
            raise RuntimeError(msg)
        w, h = self._codec_size
        # Clamp into the device frame; scrcpy ignores out-of-bounds events
        # silently which makes debugging confusing.
        x = max(0, min(w - 1, int(x)))
        y = max(0, min(h - 1, int(y)))
        # 1 + 1 + 8 + 4 + 4 + 2 + 2 + 2 + 4 + 4 = 32 bytes.
        msg_bytes = struct.pack(
            ">BBQiiHHHII",
            _CTRL_INJECT_TOUCH_EVENT,
            action & 0xFF,
            pointer_id & 0xFFFFFFFFFFFFFFFF,
            x,
            y,
            w & 0xFFFF,
            h & 0xFFFF,
            pressure & 0xFFFF,
            _BUTTON_PRIMARY,
            buttons & 0xFFFFFFFF,
        )
        with self._control_lock:
            self._control_sock.sendall(msg_bytes)

    def tap(self, x: int, y: int) -> None:
        """Single-finger tap at device-physical pixel (x, y)."""
        self._send_touch(_ACTION_DOWN, x, y, pressure=_PRESSURE_DOWN, buttons=_BUTTON_PRIMARY)
        hold_s = _gauss_between(_TAP_HOLD_MS_MIN, _TAP_HOLD_MS_MAX) / 1000.0
        if random.random() < _TAP_MICRO_MOVE_PROBABILITY:
            time.sleep(hold_s * _gauss_between(0.35, 0.7))
            mx = x + random.choice((-1, 1)) * random.randint(1, 2)
            my = y + random.choice((-1, 1)) * random.randint(1, 2)
            self._send_touch(
                _ACTION_MOVE,
                mx,
                my,
                pressure=random.randint(_PRESSURE_MOVE_MIN, _PRESSURE_DOWN),
                buttons=_BUTTON_PRIMARY,
            )
            time.sleep(hold_s * _gauss_between(0.2, 0.45))
        else:
            time.sleep(hold_s)
        self._send_touch(_ACTION_UP, x, y, pressure=_PRESSURE_UP, buttons=0)

    def long_press(self, x: int, y: int, *, duration_ms: int = 800) -> None:
        self._send_touch(_ACTION_DOWN, x, y, pressure=_PRESSURE_DOWN, buttons=_BUTTON_PRIMARY)
        time.sleep(max(0.001, duration_ms / 1000.0))
        self._send_touch(_ACTION_UP, x, y, pressure=_PRESSURE_UP, buttons=0)

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        *,
        duration_ms: int = 300,
        steps: int = 16,
    ) -> None:
        """Human-shaped swipe from (x1,y1) to (x2,y2). Coords are device px.

        scrcpy control messages are low-latency enough that we can send a
        realistic finger trail instead of one perfectly linear, evenly-spaced
        ``adb shell input swipe``. The path uses a small perpendicular curve,
        smoothstep acceleration/deceleration, per-point subpixel jitter, and
        slightly uneven host sleeps. Long-presses (same start/end) are handled
        by :meth:`long_press` at the controller layer and should not call this.
        """
        n = max(2, int(steps))
        points = _human_swipe_points(x1, y1, x2, y2, steps=n)
        sleeps = _human_step_sleeps(duration_ms, steps=len(points))
        down_pressure = random.randint(_PRESSURE_MOVE_MIN, _PRESSURE_DOWN)
        self._send_touch(
            _ACTION_DOWN, x1, y1, pressure=down_pressure, buttons=_BUTTON_PRIMARY
        )
        for (mx, my), step_sleep in zip(points, sleeps, strict=False):
            time.sleep(step_sleep)
            pressure = random.randint(_PRESSURE_MOVE_MIN, _PRESSURE_DOWN)
            self._send_touch(
                _ACTION_MOVE,
                mx,
                my,
                pressure=pressure,
                buttons=_BUTTON_PRIMARY,
            )
        self._send_touch(_ACTION_UP, x2, y2, pressure=_PRESSURE_UP, buttons=0)

    # -- context manager sugar ---------------------------------------------

    def __enter__(self) -> ScrcpyClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# process-wide registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ScrcpyClient] = {}
_REGISTRY_LOCK = threading.Lock()


def get_or_create_scrcpy_client(
    serial: str,
    adb_bin: str,
    *,
    port: int = DEFAULT_PORT_BASE,
) -> ScrcpyClient:
    """Process-wide singleton per ADB serial.

    Both :class:`adb.bot_actions.BotActions` (screenshot path) and
    :class:`adb.controller.AdbController` (input path) call this so they share
    one server process / video socket / control socket per device. Caller is
    responsible for ``start()``; this only returns the registered instance.

    The registry is keyed by ``serial`` alone — the first caller wins on
    ``adb_bin`` / ``port``. Read-only consumers (e.g. WebSocket video stream)
    must use :func:`lookup_scrcpy_client` instead so an early UI probe can't
    register a half-configured client before the worker assigns its own
    instance-slot port + resolved adb binary.
    """
    with _REGISTRY_LOCK:
        client = _REGISTRY.get(serial)
        if client is None:
            client = ScrcpyClient(serial=serial, adb_bin=adb_bin, port=port)
            _REGISTRY[serial] = client
        return client


def lookup_scrcpy_client(serial: str) -> ScrcpyClient | None:
    """Return the registered client for ``serial`` or ``None`` — never creates.

    Use this from consumers that only *observe* an existing client (e.g. the
    WebSocket H.264 stream route, which subscribes to NAL fan-out from a
    server already started by the worker). Creating a new client here would
    poison the registry with a default-port / default-adb instance that the
    worker can never replace, breaking scrcpy start after the first UI probe.
    """
    with _REGISTRY_LOCK:
        return _REGISTRY.get(serial)


def close_scrcpy_client(serial: str) -> None:
    """Tear down the registered client for ``serial`` (no-op if absent)."""
    with _REGISTRY_LOCK:
        client = _REGISTRY.pop(serial, None)
    if client is not None:
        with contextlib.suppress(Exception):
            client.close()


def close_all_scrcpy_clients() -> None:
    with _REGISTRY_LOCK:
        clients = list(_REGISTRY.values())
        _REGISTRY.clear()
    for client in clients:
        with contextlib.suppress(Exception):
            client.close()
