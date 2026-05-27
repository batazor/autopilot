"""scrcpy (Genymobile) screen capture + input via a single shared server process.

scrcpy ships a Java/DEX server pushed to ``/data/local/tmp/scrcpy-server.jar``
and launched through ``app_process``. It opens a forwarded local abstract
socket on which the host opens up to three connections, in this fixed order:

    1. video socket  (when video=true)
    2. audio socket  (when audio=true) — we leave it off
    3. control socket (when control=true)

The video socket streams raw H.264 NAL units with a 12-byte per-packet header
(PTS uint64 BE + size uint32 BE). The top two PTS bits are flags
(config / key-frame). The control socket is bidirectional binary; each touch
event is a 32-byte message in the format Server.java decodes in
``ControlMessageReader``.

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

if TYPE_CHECKING:
    from collections.abc import Iterable

    import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

SCRCPY_SERVER_VERSION = "3.1"
_SERVER_URL = (
    f"https://github.com/Genymobile/scrcpy/releases/download/v{SCRCPY_SERVER_VERSION}"
    f"/scrcpy-server-v{SCRCPY_SERVER_VERSION}"
)
DEVICE_TMP = "/data/local/tmp"
DEVICE_JAR = f"{DEVICE_TMP}/scrcpy-server.jar"
DEFAULT_PORT_BASE = 1515
_DOWNLOAD_CACHE = Path.home() / ".cache" / "wos-autopilot" / "scrcpy"

# Frame meta flags (top bits of the 64-bit PTS).
_PACKET_FLAG_CONFIG = 1 << 63
_PACKET_FLAG_KEY_FRAME = 1 << 62
_PTS_MASK = (1 << 62) - 1

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

# Server send_dummy_byte=true sends one 0x00 byte on the first connection
# (the video socket) so the host knows ``adb forward`` actually reached the
# device-side socket (forward succeeds even before the server binds).
_DUMMY_BYTE = b"\x00"


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
            parts = out.split()
            if len(parts) >= 5:
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
        stderr = (exc.stderr or b"").decode(errors="replace").strip()
        status.last_error = f"{exc.cmd}: {stderr or exc}"
        return status
    except Exception as exc:
        status.last_error = str(exc)
        return status
    return get_scrcpy_status(serial, adb_bin)


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
        if self.is_alive():
            return

        status = get_scrcpy_status(self.serial, self.adb_bin)
        if not status.installed:
            logger.info("scrcpy: jar not installed on %s — installing", self.serial)
            status = install_scrcpy(self.serial, self.adb_bin)
            if not status.installed:
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
        # device-side abstract socket and accepts. Order on the device side
        # determines socket roles (video → control here, audio off).
        server_args = [
            f"scid={self._scid}",
            "log_level=warn",
            "audio=false",
            "video=true",
            "control=true",
            "video_codec=h264",
            f"video_bit_rate={self.video_bit_rate}",
            f"max_size={self.max_size}",
            f"max_fps={self.max_fps}",
            "tunnel_forward=true",
            "show_touches=false",
            "stay_awake=false",
            "power_off_on_close=false",
            "clipboard_autosync=false",
            "cleanup=true",
            "send_device_meta=true",
            "send_frame_meta=true",
            "send_dummy_byte=true",
            "send_codec_meta=true",
            "raw_stream=false",
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
        # Server takes ~400-700ms to bind on cold start (BlueStacks slower).
        time.sleep(0.8)
        if proc.poll() is not None:
            stderr = (proc.stderr.read() if proc.stderr else b"").decode(errors="replace")
            self._last_error = stderr.strip() or "(no stderr)"
            msg = f"scrcpy server exited immediately: {self._last_error}"
            raise RuntimeError(msg)
        self._proc = proc

        _run_adb(
            ["forward", f"tcp:{self.port}", f"localabstract:{self._abstract_name}"],
            serial=self.serial, adb_bin=self.adb_bin, check=True,
        )

        try:
            self._connect_sockets()
        except Exception:
            self.close()
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
        first socket is the ``send_dummy_byte`` synchronisation marker; the
        device meta + codec meta follow on the video socket only.
        """
        # Video socket — accepts the dummy byte + device meta + codec meta.
        video = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        video.settimeout(8.0)
        # Server bind can race adb forward; retry briefly.
        for attempt in range(20):
            try:
                video.connect(("127.0.0.1", self.port))
                break
            except (ConnectionRefusedError, OSError):
                if attempt == 19:
                    raise
                time.sleep(0.1)
        dummy = _recv_exact(video, 1)
        if dummy != _DUMMY_BYTE:
            msg = f"scrcpy: unexpected first byte on video socket: {dummy!r}"
            raise RuntimeError(msg)
        device_name_raw = _recv_exact(video, 64)
        self._device_name = device_name_raw.split(b"\x00", 1)[0].decode(
            "utf-8", errors="replace"
        )
        codec_meta = _recv_exact(video, 12)
        codec_id = codec_meta[0:4]
        width, height = struct.unpack(">II", codec_meta[4:12])
        if codec_id != b"h264":
            msg = f"scrcpy: unexpected video codec id: {codec_id!r}"
            raise RuntimeError(msg)
        self._codec_size = (int(width), int(height))
        video.settimeout(None)
        self._video_sock = video

        # Control socket — same forwarded port, second accept.
        control = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        control.settimeout(5.0)
        control.connect(("127.0.0.1", self.port))
        control.settimeout(None)
        self._control_sock = control

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
        decoder = _H264Decoder()
        while not self._stop.is_set():
            try:
                header = _recv_exact(sock, 12)
                _pts_flags = struct.unpack(">Q", header[0:8])[0]
                size = struct.unpack(">I", header[8:12])[0]
                if size == 0:
                    continue
                payload = _recv_exact(sock, size)
            except (ConnectionError, OSError) as exc:
                if not self._stop.is_set():
                    self._last_error = f"video socket read failed: {exc}"
                    logger.warning("scrcpy %s: %s", self.serial, self._last_error)
                return
            frames = decoder.decode(payload)
            if not frames:
                continue
            # Use the most recent frame (skip older ones in a multi-frame batch).
            img = frames[-1]
            with self._cache_lock:
                self._cache = _CachedFrame(image=img, captured_at=time.monotonic())
            self._frame_event.set()

    def read_latest_frame_bgr(
        self, timeout_s: float = 0.5
    ) -> tuple[np.ndarray | None, str]:
        """Return (BGR frame, error). Cached frame returned if no new one within timeout."""
        if not self.is_alive():
            return None, self._last_error or "scrcpy not started"
        self._frame_event.clear()
        if not self._frame_event.wait(timeout=timeout_s):
            with self._cache_lock:
                cached = self._cache
            if cached is not None:
                return cached.image, ""
            return None, "no frame received yet"
        with self._cache_lock:
            cached = self._cache
        if cached is None:
            return None, "frame event fired but cache empty"
        return cached.image, ""

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
        # 30 ms hold is enough to register as a tap on every Android version we
        # care about and short enough to feel instant. Sleep on the host since
        # the control protocol has no server-side "wait" command.
        time.sleep(0.03)
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
        """Straight swipe from (x1,y1) to (x2,y2). Coords are device-physical px."""
        n = max(2, int(steps))
        step_sleep = max(0.001, (duration_ms / 1000.0) / n)
        self._send_touch(_ACTION_DOWN, x1, y1, pressure=_PRESSURE_DOWN, buttons=_BUTTON_PRIMARY)
        for i in range(1, n + 1):
            t = i / n
            mx = int(round(x1 + (x2 - x1) * t))
            my = int(round(y1 + (y2 - y1) * t))
            time.sleep(step_sleep)
            self._send_touch(_ACTION_MOVE, mx, my, pressure=_PRESSURE_DOWN, buttons=_BUTTON_PRIMARY)
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
    """
    with _REGISTRY_LOCK:
        client = _REGISTRY.get(serial)
        if client is None:
            client = ScrcpyClient(serial=serial, adb_bin=adb_bin, port=port)
            _REGISTRY[serial] = client
        return client


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
