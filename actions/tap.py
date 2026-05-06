"""ADB-based input controller for BlueStacks instances.

All touch input and framebuffer capture use ``adb`` (no macOS Quartz window capture).
"""

from __future__ import annotations

import json
import logging
import random
import subprocess
import time
import uuid
from datetime import timedelta

import numpy as np
import redis

from capture.adb_screencap import (
    DEFAULT_ADB_BIN,
    MSG_ADB_NOT_FOUND,
    adb_screencap_bgr,
    resolve_adb_executable,
)
from config.loader import get_settings
from layout.types import Point, Region

logger = logging.getLogger(__name__)

_GAME_PACKAGE = "com.gof.global"

_redis_client: redis.Redis | None = None


def _redis() -> redis.Redis:
    """Lazy sync Redis client for UI click approvals."""
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = redis.Redis.from_url(settings.redis.url, decode_responses=True)
    return _redis_client


def _require_approval(instance_id: str, payload: dict[str, object]) -> tuple[bool, str | None]:
    """If approval mode is enabled, block until UI approves/rejects.

    Contract (no stack):
    - At most one pending request per instance stored at
      ``wos:ui:click_approval:current:<instance_id>``.
    - UI writes decision to ``wos:ui:click_approval:response:<instance_id>``.
    """
    enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
    enabled = str(_redis().get(enabled_key) or "").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return True, None

    hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
    # Safety: enabled but page not open => block all inputs.
    if not _redis().get(hb_key):
        return False, None

    current_key = f"wos:ui:click_approval:current:{instance_id}"
    resp_key = f"wos:ui:click_approval:response:{instance_id}"

    req_id: str | None = None

    # Create a new request only if there isn't one already.
    if not _redis().get(current_key):
        req_id = f"adb:{instance_id}:{uuid.uuid4().hex[:12]}"

        # Attach context for debugging ("who" + "why").
        ctx: dict[str, object] = {}
        try:
            inst_state_key = f"wos:instance:{instance_id}:state"
            raw = _redis().hgetall(inst_state_key)
            if raw:
                ctx = {
                    "current_screen": (raw.get("current_screen") or "").strip(),
                    "current_task_type": (raw.get("current_task_type") or "").strip(),
                    "current_task_id": (raw.get("current_task_id") or "").strip(),
                    "current_task_player": (raw.get("current_task_player") or "").strip(),
                    "current_task_region": (raw.get("current_task_region") or "").strip(),
                    "current_task_threshold": (raw.get("current_task_threshold") or "").strip(),
                }
        except Exception:
            ctx = {}

        p = dict(payload)
        p.update(
            {
                "request_id": req_id,
                "instance_id": instance_id,
                "created_at": time.time(),
                "status": "waiting",
                "source": {
                    "component": "actions.tap.AdbController",
                    "note": "ADB input request (approval mode enabled)",
                },
                "context": ctx,
            }
        )

        # Promote overlay threshold to top-level for convenience (when present).
        thr_s = str(ctx.get("current_task_threshold") or "").strip()
        if thr_s and "threshold" not in p:
            try:
                p["threshold"] = float(thr_s)
            except ValueError:
                pass

        # Clear stale decision and store "current" request (single slot).
        try:
            _redis().delete(resp_key)
        except Exception:
            pass
        _redis().set(current_key, json.dumps(p), ex=120)
    else:
        # Reuse existing request id (if present) so the caller can mark execution.
        try:
            raw_existing = _redis().get(current_key)
            if raw_existing:
                req_id = str(json.loads(raw_existing).get("request_id") or "") or None
        except Exception:
            req_id = None

    deadline = time.time() + 60.0
    decision: str | None = None
    while time.time() < deadline:
        raw = _redis().get(resp_key)
        if raw:
            decision = str(raw).strip().lower()
            break
        time.sleep(0.2)

    if decision in {"approve", "reject"}:
        # Persist decision time on the current payload for UI/debug.
        try:
            raw_cur = _redis().get(current_key)
            if raw_cur:
                doc = json.loads(raw_cur)
                doc["decision"] = decision
                doc["approved_at"] = time.time() if decision == "approve" else None
                doc["rejected_at"] = time.time() if decision == "reject" else None
                doc["status"] = "approved" if decision == "approve" else "rejected"
                _redis().set(current_key, json.dumps(doc), ex=120)
        except Exception:
            logger.debug("Failed to mark decision timestamps", exc_info=True)

    # On reject/timeout, clear slot so the bot can proceed.
    if decision != "approve":
        try:
            _redis().delete(current_key)
            _redis().delete(resp_key)
        except Exception:
            logger.debug("approval cleanup failed", exc_info=True)

    return decision == "approve", req_id


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def _jitter(value: int, spread: int) -> int:
    """Apply ±spread pixel random jitter."""
    if spread <= 0:
        return value
    return value + random.randint(-spread, spread)


class AdbController:
    """ADB wrapper matching the Go DeviceController interface."""

    def __init__(self, instance_id: str, device_serial: str, *, adb_bin: str = DEFAULT_ADB_BIN) -> None:
        resolved = resolve_adb_executable(adb_bin)
        if resolved is None:
            raise RuntimeError(MSG_ADB_NOT_FOUND)
        self._instance_id = instance_id
        self._adb_exe = resolved
        self._serial = device_serial
        self._verify_available()
        self.set_brightness(70)
        self.set_heads_up_notifications(enabled=False)

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    @staticmethod
    def list_devices(adb_bin: str = DEFAULT_ADB_BIN) -> list[str]:
        resolved = resolve_adb_executable(adb_bin)
        if resolved is None:
            raise RuntimeError(MSG_ADB_NOT_FOUND)
        proc = subprocess.run(
            [resolved, "devices"],
            capture_output=True,
            text=True,
            check=False,
        )
        out = proc.stdout or ""
        serials: list[str] = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                serials.append(parts[0])
        return serials

    def set_active_device(self, serial: str) -> None:
        self._serial = serial

    def get_active_device(self) -> str:
        return self._serial

    def _verify_available(self) -> None:
        proc = subprocess.run(
            [self._adb_exe, "devices"],
            capture_output=True,
            text=True,
            check=False,
        )
        out = proc.stdout or ""
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[0] == self._serial and parts[1] == "device":
                return
        raise RuntimeError(
            f"ADB device '{self._serial}' not found or not in 'device' state.\n"
            f"Connected devices:\n{out}"
        )

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def set_brightness(self, percent: int) -> None:
        percent = _clamp(percent, 0, 100)
        value = int(percent / 100.0 * 255)
        self._shell("settings", "put", "system", "screen_brightness", str(value))
        logger.debug("Brightness set to %d%% (%d/255) on %s", percent, value, self._serial)

    def set_heads_up_notifications(self, enabled: bool) -> None:
        value = "1" if enabled else "0"
        self._shell("settings", "put", "global", "heads_up_notifications_enabled", value)

    def get_screen_resolution(self) -> tuple[int, int]:
        out = self._shell("wm", "size")
        for line in out.splitlines():
            if "Physical size:" in line or "Override size:" in line:
                parts = line.split()
                if parts:
                    w_str, _, h_str = parts[-1].partition("x")
                    if w_str.isdigit() and h_str.isdigit():
                        return int(w_str), int(h_str)
        raise RuntimeError(f"Cannot parse screen resolution from: {out!r}")

    # ------------------------------------------------------------------
    # App lifecycle
    # ------------------------------------------------------------------

    def restart_application(self) -> None:
        logger.warning("Restarting %s on %s", _GAME_PACKAGE, self._serial)
        self._shell("am", "force-stop", _GAME_PACKAGE)
        time.sleep(2)
        self._shell("monkey", "-p", _GAME_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        logger.info("Application restarted on %s", self._serial)

    def is_game_foreground(self) -> bool:
        """True if dumpsys reports Whiteout as the resumed / top activity on this device."""
        out = self._shell("dumpsys", "activity", "activities")
        markers = ("topResumedActivity=", "ResumedActivity:", "mResumedActivity:")
        for line in out.splitlines():
            s = line.strip()
            if _GAME_PACKAGE not in line:
                continue
            if any(m in s for m in markers):
                return True
        return False

    def ensure_game_foreground(self) -> None:
        """Start Whiteout if it is not the foreground resumed activity (ADB serial device)."""
        if self.is_game_foreground():
            logger.info("Whiteout already foreground (%s on %s)", _GAME_PACKAGE, self._serial)
            return
        logger.warning(
            "Whiteout not in foreground — launching %s on %s", _GAME_PACKAGE, self._serial
        )
        self._shell("monkey", "-p", _GAME_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(2)

    # ------------------------------------------------------------------
    # Touch input — all with jitter to simulate natural fingers
    # ------------------------------------------------------------------

    def tap(self, point: Point) -> None:
        """Tap center of Point with ±2 px jitter."""
        x = _jitter(point.x, 2)
        y = _jitter(point.y, 2)
        ok, req_id = _require_approval(
            self._instance_id,
            {"type": "tap", "x": int(x), "y": int(y), "serial": self._serial},
        )
        if not ok:
            logger.info("ADB tap blocked (no approval): %s (%d,%d)", self._instance_id, x, y)
            return
        if req_id is not None:
            try:
                current_key = f"wos:ui:click_approval:current:{self._instance_id}"
                raw = _redis().get(current_key)
                if raw:
                    doc = json.loads(raw)
                    doc["executed_at"] = time.time()
                    doc["status"] = "executing"
                    _redis().set(current_key, json.dumps(doc), ex=120)
            except Exception:
                logger.debug("Failed to mark executed_at", exc_info=True)
        self._shell("input", "tap", str(x), str(y))
        logger.debug("Tap (%d, %d) on %s", x, y, self._serial)
        if req_id is not None:
            try:
                _redis().delete(f"wos:ui:click_approval:current:{self._instance_id}")
                _redis().delete(f"wos:ui:click_approval:response:{self._instance_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys after tap", exc_info=True)

    def tap_region(self, region: Region) -> None:
        """Tap center of Region with ±5% size jitter, clamped inside bounds."""
        cx = region.x + region.w // 2
        cy = region.y + region.h // 2
        spread_x = max(1, int(region.w * 0.05))
        spread_y = max(1, int(region.h * 0.05))
        x = _clamp(_jitter(cx, spread_x), region.x, region.x + region.w - 1)
        y = _clamp(_jitter(cy, spread_y), region.y, region.y + region.h - 1)
        ok, req_id = _require_approval(
            self._instance_id,
            {
                "type": "tap_region",
                "x": int(x),
                "y": int(y),
                "serial": self._serial,
                "region": {"x": region.x, "y": region.y, "w": region.w, "h": region.h},
            },
        )
        if not ok:
            logger.info("ADB tap_region blocked (no approval): %s (%d,%d)", self._instance_id, x, y)
            return
        if req_id is not None:
            try:
                current_key = f"wos:ui:click_approval:current:{self._instance_id}"
                raw = _redis().get(current_key)
                if raw:
                    doc = json.loads(raw)
                    doc["executed_at"] = time.time()
                    doc["status"] = "executing"
                    _redis().set(current_key, json.dumps(doc), ex=120)
            except Exception:
                logger.debug("Failed to mark executed_at", exc_info=True)
        self._shell("input", "tap", str(x), str(y))
        logger.debug("TapRegion (%d, %d) on %s", x, y, self._serial)
        if req_id is not None:
            try:
                _redis().delete(f"wos:ui:click_approval:current:{self._instance_id}")
                _redis().delete(f"wos:ui:click_approval:response:{self._instance_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys after tap_region", exc_info=True)

    def swipe(
        self,
        start: Point,
        end: Point,
        duration: timedelta = timedelta(milliseconds=300),
    ) -> None:
        """Swipe with ±2 px jitter on all coordinates."""
        x1 = _jitter(start.x, 2)
        y1 = _jitter(start.y, 2)
        x2 = _jitter(end.x, 2)
        y2 = _jitter(end.y, 2)
        ms = int(duration.total_seconds() * 1000)
        ok, req_id = _require_approval(
            self._instance_id,
            {
                "type": "swipe",
                "x1": int(x1),
                "y1": int(y1),
                "x2": int(x2),
                "y2": int(y2),
                "ms": int(ms),
                "serial": self._serial,
            },
        )
        if not ok:
            logger.info("ADB swipe blocked (no approval): %s", self._instance_id)
            return
        if req_id is not None:
            try:
                current_key = f"wos:ui:click_approval:current:{self._instance_id}"
                raw = _redis().get(current_key)
                if raw:
                    doc = json.loads(raw)
                    doc["executed_at"] = time.time()
                    doc["status"] = "executing"
                    _redis().set(current_key, json.dumps(doc), ex=120)
            except Exception:
                logger.debug("Failed to mark executed_at", exc_info=True)
        self._shell("input", "touchscreen", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))
        logger.debug("Swipe (%d,%d)→(%d,%d) %dms on %s", x1, y1, x2, y2, ms, self._serial)
        if req_id is not None:
            try:
                _redis().delete(f"wos:ui:click_approval:current:{self._instance_id}")
                _redis().delete(f"wos:ui:click_approval:response:{self._instance_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys after swipe", exc_info=True)

    def swipe_direction(
        self,
        direction: str,
        delta: int,
        duration: timedelta = timedelta(milliseconds=300),
    ) -> None:
        """Swipe left/right/up/down by delta pixels from screen center."""
        w, h = self.get_screen_resolution()
        cx, cy = w // 2, h // 2
        match direction.lower():
            case "left":
                start, end = Point(cx, cy), Point(cx - delta, cy)
            case "right":
                start, end = Point(cx, cy), Point(cx + delta, cy)
            case "up":
                start, end = Point(cx, cy), Point(cx, cy - delta)
            case "down":
                start, end = Point(cx, cy), Point(cx, cy + delta)
            case _:
                raise ValueError(f"Unknown swipe direction: {direction!r}")
        self.swipe(start, end, duration)

    def long_tap(self, point: Point, duration: timedelta = timedelta(milliseconds=800)) -> None:
        """Long-press via swipe with same start/end coords."""
        self.swipe(point, point, duration)

    def type_text(self, text: str) -> None:
        escaped = text.replace(" ", "%s").replace("'", "\\'")
        ok, req_id = _require_approval(
            self._instance_id,
            {"type": "type_text", "text": text, "serial": self._serial},
        )
        if not ok:
            logger.info("ADB type_text blocked (no approval): %s", self._instance_id)
            return
        if req_id is not None:
            try:
                current_key = f"wos:ui:click_approval:current:{self._instance_id}"
                raw = _redis().get(current_key)
                if raw:
                    doc = json.loads(raw)
                    doc["executed_at"] = time.time()
                    doc["status"] = "executing"
                    _redis().set(current_key, json.dumps(doc), ex=120)
            except Exception:
                logger.debug("Failed to mark executed_at", exc_info=True)
        self._shell("input", "text", escaped)
        logger.debug("Type '%s' on %s", text, self._serial)
        if req_id is not None:
            try:
                _redis().delete(f"wos:ui:click_approval:current:{self._instance_id}")
                _redis().delete(f"wos:ui:click_approval:response:{self._instance_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys after type_text", exc_info=True)

    # ------------------------------------------------------------------
    # Screenshot via ADB
    # ------------------------------------------------------------------

    def screenshot_bytes(self) -> bytes:
        from capture.adb_screencap import adb_screencap_png

        data, err = adb_screencap_png(self._adb_exe, self._serial)
        if data is None:
            raise RuntimeError(err)
        return data

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _shell(self, *args: str) -> str:
        result = subprocess.run(
            [self._adb_exe, "-s", self._serial, "shell"] + list(args),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(
                "ADB shell %s failed (rc=%d): %s", args, result.returncode, result.stderr.strip()
            )
        return result.stdout.strip()


# ---------------------------------------------------------------------------
# BotActions — instance-aware facade used by tasks and the use case executor
# ---------------------------------------------------------------------------


class BotActions:
    """Instance-aware facade: resolves instance_id → ADB serial → AdbController."""

    def __init__(self) -> None:
        self._settings = get_settings()
        self._controllers: dict[str, AdbController] = {}

    def _controller(self, instance_id: str) -> AdbController:
        if instance_id not in self._controllers:
            serial = self._get_serial(instance_id)
            self._controllers[instance_id] = AdbController(
                instance_id,
                serial,
                adb_bin=self._adb_bin(),
            )
        return self._controllers[instance_id]

    def _get_serial(self, instance_id: str) -> str:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return inst.bluestacks_window_title  # ADB serial for BlueStacks
        raise ValueError(f"Unknown instance_id: {instance_id!r}")

    def _adb_bin(self) -> str:
        pref = (self._settings.worker.adb_executable or "").strip()
        return pref if pref else DEFAULT_ADB_BIN

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        """Framebuffer BGR via ``adb exec-out screencap -p`` (same coordinate space as taps)."""
        img, err = adb_screencap_bgr(self._adb_bin(), self._get_serial(instance_id))
        if img is None:
            raise RuntimeError(err)
        return img

    def tap(self, instance_id: str, point: Point) -> None:
        self._controller(instance_id).tap(point)

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        """Emulator framebuffer size from ``adb shell wm size`` (tap coordinate space)."""
        return self._controller(instance_id).get_screen_resolution()

    def tap_region(self, instance_id: str, region: Region) -> None:
        self._controller(instance_id).tap_region(region)

    def swipe(
        self,
        instance_id: str,
        start: Point,
        end: Point,
        duration_ms: int = 300,
    ) -> None:
        self._controller(instance_id).swipe(start, end, timedelta(milliseconds=duration_ms))

    def swipe_direction(
        self, instance_id: str, direction: str, delta: int, duration_ms: int = 300
    ) -> None:
        self._controller(instance_id).swipe_direction(
            direction, delta, timedelta(milliseconds=duration_ms)
        )

    def long_tap(self, instance_id: str, point: Point, duration_ms: int = 800) -> None:
        self._controller(instance_id).long_tap(point, timedelta(milliseconds=duration_ms))

    def back(self, instance_id: str) -> None:
        logger.debug("BotActions.back(%s): no-op (phone BACK not allowed)", instance_id)

    def home(self, instance_id: str) -> None:
        logger.debug("BotActions.home(%s): no-op (phone HOME not allowed)", instance_id)

    def type_text(self, instance_id: str, text: str) -> None:
        self._controller(instance_id).type_text(text)

    def restart_application(self, instance_id: str) -> None:
        self._controller(instance_id).restart_application()

    def ensure_game_foreground(self, instance_id: str) -> None:
        self._controller(instance_id).ensure_game_foreground()
