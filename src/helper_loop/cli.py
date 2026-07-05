"""``play-helper`` — minimal standalone alliance-help clicker.

A tight, dependency-light loop that does ONLY two things per device, every
~0.5s, with no worker / scheduler / overlay engine / OCR HTTP service:

1. grab a frame — scrcpy H.264 stream (default, fps-capped, no per-frame adb)
   or RAW ``adb exec-out screencap`` (``--backend adb``);
2. template-match the alliance help icon in its region → tap it;
3. template-match the chat title ("Чат") → if we slipped into the chat tab
   (clicked too late), tap the back button to escape.

Everything is in-process cv2 template matching against reference crops shipped
with the modules, so CPU stays low. Coordinates/templates come from the same
``area.yaml`` regions the full bot uses (cited inline) — devices are 720x1280,
matching the reference scale, so no rescaling is needed.

Run:   uv run play-helper                 # all registered devices
       uv run play-helper -d bs1,bs2      # subset
       uv run play-helper --once --dry-run -v   # one pass, detect only
"""
from __future__ import annotations

import argparse
import contextlib
import logging
import random
import struct
import subprocess
import threading
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

from adb import resolve_adb_executable
from adb.frame_normalize import GAME_FRAME_SIZE
from adb.scrcpy import DEFAULT_PORT_BASE, close_all_scrcpy_clients, get_or_create_scrcpy_client
from adb.screencap import DEFAULT_ADB_BIN, adb_screencap_bgr
from config.devices import get_device_registry
from config.paths import repo_root
from layout.template_match import match_template_in_search_roi_bbox_percent

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

logger = logging.getLogger("play_helper")

# ── Regions (percent of 720x1280), verbatim from the modules' area.yaml ──────
# games/wos/alliance/helper/area.yaml → button.alliance.help
HELP_BBOX = {"x": 69.6357391761772, "y": 85.98686358461053,
             "width": 7.109658277834156, "height": 5.253318747044119}
# games/wos/chat/area.yaml → chat.title  (RU shows "Чат")
CHAT_TITLE_BBOX = {"x": 11.687141209950434, "y": 0.9764946866410164,
                   "width": 13.525280898876392, "height": 4.215132897460327}
# games/wos/core/chief_profile/area.yaml → icon.page.back
BACK_BBOX = {"x": 0.7702702702702703, "y": 0.5163043478260869,
             "width": 10.832046332046332, "height": 4.95}

# ── Reference crops ──────────────────────────────────────────────────────────
HELP_CROP = "games/wos/alliance/helper/references/crop/alliance.helper_button.alliance.help.png"
CHAT_REF_RU = "games/wos/ru/chat/references/chat.alliance.png"  # full frame; title cropped at runtime

DEFAULT_INTERVAL = 0.5
DEFAULT_HELP_THRESHOLD = 0.80
DEFAULT_CHAT_THRESHOLD = 0.80
DEFAULT_LAG_MIN = 0.20  # random pre-tap delay (sec), human-like / lets the UI settle
DEFAULT_LAG_MAX = 0.30
DEFAULT_BACKEND = "scrcpy"  # H.264 stream: no per-frame adb spawn / 3.7MB transfer
DEFAULT_FPS_CAP = 4  # scrcpy max_fps — low keeps emulator encode + host decode cheap
DEFAULT_CLICK_TIMEOUT = 5.0  # min seconds between clicks per device (also debounces double-back)
RECONNECT_BACKOFF = 5.0  # min seconds between adb-connect attempts when a device drops
_SEARCH_PAD_PCT = 6.0  # widen the search ROI so the template has room to slide


def _expand(bbox: dict[str, float], pad: float = _SEARCH_PAD_PCT) -> dict[str, float]:
    return {
        "x": max(0.0, bbox["x"] - pad),
        "y": max(0.0, bbox["y"] - pad),
        "width": min(100.0, bbox["width"] + 2 * pad),
        "height": min(100.0, bbox["height"] + 2 * pad),
    }


def _bbox_center_px(bbox: dict[str, float], w: int, h: int) -> tuple[int, int]:
    cx = (bbox["x"] + bbox["width"] / 2) * w / 100.0
    cy = (bbox["y"] + bbox["height"] / 2) * h / 100.0
    return int(cx), int(cy)


def _crop_bbox_px(img: np.ndarray, bbox: dict[str, float]) -> np.ndarray:
    h, w = img.shape[:2]
    x0 = int(bbox["x"] * w / 100.0)
    y0 = int(bbox["y"] * h / 100.0)
    x1 = x0 + int(bbox["width"] * w / 100.0)
    y1 = y0 + int(bbox["height"] * h / 100.0)
    return img[y0:y1, x0:x1]


class Templates:
    """Loaded once, shared across device threads (read-only)."""

    def __init__(self, root: Path) -> None:
        help_path = root / HELP_CROP
        self.help = cv2.imread(str(help_path))
        if self.help is None:
            msg = f"help crop not found: {help_path}"
            raise FileNotFoundError(msg)
        chat_path = root / CHAT_REF_RU
        chat_ref = cv2.imread(str(chat_path))
        if chat_ref is None:
            msg = f"chat reference not found: {chat_path}"
            raise FileNotFoundError(msg)
        # The "Чат" title crop, lifted from the RU reference at the title bbox.
        self.chat_title = _crop_bbox_px(chat_ref, CHAT_TITLE_BBOX)


def _sleep_lag(args: argparse.Namespace) -> float:
    """Sleep a random pre-tap lag in [lag_min, lag_max]; returns the slept seconds."""
    lag = random.uniform(args.lag_min, args.lag_max) if args.lag_max > 0 else 0.0
    if lag > 0:
        time.sleep(lag)
    return lag


def _screencap_bgr(adb_bin: str, serial: str) -> tuple[np.ndarray | None, str]:
    """Capture a BGR frame via RAW screencap (no ``-p``).

    Skips the on-device PNG encode (saves emulator CPU) and decodes with a numpy
    reshape (~2.5ms) instead of cv2.imdecode (~8ms). Falls back to the project's
    robust PNG path on any shape/format surprise.
    """
    try:
        out = subprocess.run(
            [adb_bin, "-s", serial, "exec-out", "screencap"],
            capture_output=True, timeout=10, check=False,
        ).stdout
        if len(out) >= 12:
            w, h, _fmt = struct.unpack("<III", out[:12])
            need = w * h * 4
            hdr = len(out) - need  # 12 (legacy) or 16 (Android 12+ adds colorspace)
            if w > 0 and h > 0 and hdr in (12, 16):
                rgba = np.frombuffer(out[hdr:hdr + need], dtype=np.uint8).reshape(h, w, 4)
                return cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGR), ""
    except Exception as exc:
        logger.debug("raw screencap failed (%s), falling back to PNG", exc)
    return adb_screencap_bgr(adb_bin, serial)


def _tap(adb_bin: str, serial: str, x: int, y: int) -> None:
    subprocess.run(
        [adb_bin, "-s", serial, "shell", "input", "tap", str(x), str(y)],
        timeout=10, capture_output=True, check=False,
    )


def _match(
    frame: np.ndarray, template: np.ndarray, bbox: dict[str, float], threshold: float,
) -> tuple[bool, float, int, int]:
    """Return (hit, score, tap_x, tap_y) for ``template`` within ``bbox``."""
    res = match_template_in_search_roi_bbox_percent(
        frame, template, _expand(bbox), threshold=threshold,
    )
    score = float(res.get("score") or 0.0)
    tx, ty = res.get("top_left", (0, 0))
    tap_x = int(tx) + int(res.get("template_w", template.shape[1])) // 2
    tap_y = int(ty) + int(res.get("template_h", template.shape[0])) // 2
    return score >= threshold, score, tap_x, tap_y


class _DeviceState:
    """Per-device minimum gap between clicks (also debounces the chat-back tap)."""

    def __init__(self, gap_s: float) -> None:
        self.gap_s = gap_s
        self.cooldown_until = 0.0

    def record_tap(self, now: float) -> None:
        self.cooldown_until = now + self.gap_s


def _detect(
    frame: np.ndarray, serial: str, tpl: Templates, args: argparse.Namespace,
) -> tuple[str, float, int, int] | None:
    """Detect the action to take: (kind, score, x, y) or None. Does not tap."""
    h, w = frame.shape[:2]
    # Chat-escape first: if we clicked too late and landed in chat, go back.
    chat_hit, chat_score, _, _ = _match(frame, tpl.chat_title, CHAT_TITLE_BBOX, args.chat_threshold)
    if chat_hit:
        bx, by = _bbox_center_px(BACK_BBOX, w, h)
        return "chat→back", chat_score, bx, by
    help_hit, help_score, hx, hy = _match(frame, tpl.help, HELP_BBOX, args.help_threshold)
    if help_hit:
        return "help", help_score, hx, hy
    if args.verbose:
        logger.debug("[%s] no-op (help=%.3f chat=%.3f)", serial, help_score, chat_score)
    return None


def _act(
    serial: str, adb_bin: str, state: _DeviceState,
    action: tuple[str, float, int, int], args: argparse.Namespace,
) -> None:
    """Tap (unless dry-run), then arm the per-device click timeout."""
    kind, score, x, y = action
    logger.info("[%s] %s (score=%.3f) → tap @(%d,%d)%s",
                serial, kind, score, x, y, " [dry]" if args.dry_run else "")
    if not args.dry_run:
        _sleep_lag(args)
        _tap(adb_bin, serial, x, y)
    state.record_tap(time.monotonic())  # arms the gap until the next allowed click


def _adb_connect(name: str, adb_bin: str, serial: str) -> None:
    """Best-effort ``adb connect`` — BlueStacks loses the link on adb-server restarts."""
    try:
        out = subprocess.run(
            [adb_bin, "connect", serial],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()
    except Exception as exc:
        logger.warning("[%s] adb connect %s errored: %s", name, serial, exc)
        return
    logger.info("[%s] adb connect %s: %s", name, serial, out or "(no output)")


def _looks_disconnected(err: str) -> bool:
    e = err.lower()
    return any(s in e for s in ("not found", "offline", "no devices", "cannot connect"))


def _make_capture(
    name: str, serial: str, idx: int, adb_bin: str, args: argparse.Namespace,
) -> Callable[[], tuple[np.ndarray | None, str]]:
    """Build a self-healing ``capture() -> (bgr, err)`` for the device.

    scrcpy backend: a per-device H.264 stream (max_fps capped); each tick reads
    the latest decoded frame from cache — no adb spawn / no full framebuffer
    transfer. Either backend auto ``adb connect``s + restarts when the device
    drops (throttled to once per RECONNECT_BACKOFF), so a lost link self-heals.
    """
    timeout = max(0.3, args.interval)
    next_retry = [0.0]  # mutable backoff slot shared by the capture closure

    def _due() -> bool:
        now = time.monotonic()
        if now < next_retry[0]:
            return False
        next_retry[0] = now + RECONNECT_BACKOFF
        return True

    if args.backend == "scrcpy":
        client = get_or_create_scrcpy_client(
            serial=serial, adb_bin=adb_bin, port=DEFAULT_PORT_BASE + idx)
        client.max_fps = max(0, args.fps_cap)  # read as a server launch arg

        def _ensure() -> None:
            if client.is_alive() or not _due():
                return
            _adb_connect(name, adb_bin, serial)
            try:
                client.start()
                logger.info("[%s] scrcpy up: port=%d cap=%dfps res=%s",
                            name, DEFAULT_PORT_BASE + idx, args.fps_cap, client.codec_size)
            except Exception as exc:
                logger.warning("[%s] scrcpy start failed (%s) — retry in %.0fs",
                               name, exc, RECONNECT_BACKOFF)

        _ensure()

        def capture() -> tuple[np.ndarray | None, str]:
            _ensure()
            if not client.is_alive():
                return None, "scrcpy down (reconnecting)"
            img, err = client.read_latest_frame_bgr(timeout_s=timeout)
            if img is None:
                return None, err
            if (img.shape[1], img.shape[0]) != GAME_FRAME_SIZE:
                img = cv2.resize(img, GAME_FRAME_SIZE)
            return img, ""

        return capture

    def capture() -> tuple[np.ndarray | None, str]:
        frame, err = _screencap_bgr(adb_bin, serial)
        if frame is None and _looks_disconnected(err) and _due():
            _adb_connect(name, adb_bin, serial)
        return frame, err

    return capture


def _device_loop(
    name: str, serial: str, idx: int, adb_bin: str, tpl: Templates,
    args: argparse.Namespace, stop: threading.Event,
) -> None:
    capture = _make_capture(name, serial, idx, adb_bin, args)
    logger.info("[%s] loop start serial=%s interval=%.2fs backend=%s",
                name, serial, args.interval, args.backend)
    state = _DeviceState(args.click_timeout)
    while not stop.is_set():
        t0 = time.monotonic()
        if t0 < state.cooldown_until:
            # Pause detection while: (a) debouncing after a tap so a half-finished
            # transition isn't re-tapped (a 2nd back tap on main_city opens the
            # governor profile), or (b) rate-limited, so we don't busy-spin.
            stop.wait(min(args.interval, state.cooldown_until - t0))
            continue
        frame, err = capture()
        if frame is None:
            # Capture self-heals (adb reconnect / scrcpy restart) on its own and
            # logs those attempts; keep the per-tick miss quiet to avoid spam.
            logger.debug("[%s] no frame: %s", name, err)
        else:
            try:
                action = _detect(frame, serial, tpl, args)
                if action is not None:
                    _act(serial, adb_bin, state, action, args)
            except Exception:
                logger.exception("[%s] tick error", name)
        if args.once:
            return
        # keep a steady cadence regardless of how long the tick took
        stop.wait(max(0.0, args.interval - (time.monotonic() - t0)))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="play-helper",
        description="Minimal standalone alliance-help clicker (icon tap + chat escape).",
    )
    p.add_argument("-d", "--devices", default="",
                   help="comma-separated device names (default: all registered)")
    p.add_argument("-i", "--interval", type=float, default=DEFAULT_INTERVAL,
                   help="seconds between screencaps per device (default 0.5)")
    p.add_argument("--help-threshold", type=float, default=DEFAULT_HELP_THRESHOLD)
    p.add_argument("--chat-threshold", type=float, default=DEFAULT_CHAT_THRESHOLD)
    p.add_argument("--lag-min", type=float, default=DEFAULT_LAG_MIN,
                   help="min random pre-tap delay, seconds (default 0.20)")
    p.add_argument("--lag-max", type=float, default=DEFAULT_LAG_MAX,
                   help="max random pre-tap delay, seconds (default 0.30); 0 disables")
    p.add_argument("--backend", choices=["scrcpy", "adb"], default=DEFAULT_BACKEND,
                   help="frame source: scrcpy H.264 stream (low CPU) or adb screencap")
    p.add_argument("--fps-cap", type=int, default=DEFAULT_FPS_CAP,
                   help="scrcpy max fps (low = cheap; 0 = uncapped). default 4")
    p.add_argument("--click-timeout", type=float, default=DEFAULT_CLICK_TIMEOUT,
                   help="minimum seconds between clicks per device (default 5)")
    p.add_argument("--dry-run", action="store_true", help="detect + log, never tap")
    p.add_argument("--once", action="store_true", help="single pass per device, then exit")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def _resolve_devices(spec: str) -> list[tuple[str, str]]:
    reg = get_device_registry()
    wanted = [s.strip() for s in spec.split(",") if s.strip()] if spec else None
    out: list[tuple[str, str]] = []
    for d in reg.devices:
        if wanted is not None and d.name not in wanted:
            continue
        serial = getattr(d, "effective_serial", None) or getattr(d, "adb_serial", None)
        if serial:
            out.append((d.name, serial))
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S",
    )
    root = repo_root()
    tpl = Templates(root)
    adb_bin = resolve_adb_executable() or DEFAULT_ADB_BIN

    devices = _resolve_devices(args.devices)
    if not devices:
        logger.error("no devices matched %r", args.devices or "(all)")
        return 1
    logger.info("play-helper: %d device(s): %s%s",
                len(devices), ", ".join(n for n, _ in devices),
                " [DRY-RUN]" if args.dry_run else "")

    stop = threading.Event()
    threads = [
        threading.Thread(target=_device_loop, args=(n, s, i, adb_bin, tpl, args, stop),
                         name=f"helper-{n}", daemon=True)
        for i, (n, s) in enumerate(devices)
    ]
    for t in threads:
        t.start()
    try:
        while any(t.is_alive() for t in threads):
            for t in threads:
                t.join(timeout=0.3)
    except KeyboardInterrupt:
        logger.info("stopping…")
        stop.set()
        for t in threads:
            t.join(timeout=2.0)
    finally:
        if args.backend == "scrcpy":
            with contextlib.suppress(Exception):
                close_all_scrcpy_clients()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
