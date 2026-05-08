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
_APPROVAL_POLL_SECONDS = 0.2
# Approval mode: there is intentionally NO non-decision exit from the wait
# loop — no wall-clock deadline AND no heartbeat-loss abort. The decision is
# always the operator's. The trade-off: closing the approvals page WILL hang
# the worker on this task until the page is reopened and a decision is given.
#
# How long we wait for the per-instance ``current`` slot to free up before
# giving up (only relevant if a previous request is still in flight on the
# same instance). Independent from the operator's review time.
_APPROVAL_PUBLISH_WAIT_SECONDS = 60.0
# Redis TTL for the published ``current`` key. Refreshed every iteration so
# the request never expires while the worker is still polling for a decision.
_APPROVAL_CURRENT_TTL_SECONDS = 600
_CLICK_APPROVAL_DISABLED = frozenset({"0", "false", "no", "off"})
# Copied from ``tasks.dsl_scenario`` Redis audit fields for Click approvals UI.
_DSL_APPROVAL_AUDIT_KEYS: tuple[str, ...] = (
    "dsl_last_match_region",
    "dsl_last_match_threshold",
    "dsl_last_match_score",
    "dsl_last_match_matched",
    "dsl_last_match_detail",
    "dsl_last_match_at",
    "dsl_last_ocr_region",
    "dsl_last_ocr_store",
    "dsl_last_ocr_status",
    "dsl_last_ocr_threshold",
    "dsl_last_ocr_confidence",
    "dsl_last_ocr_raw_text",
    "dsl_last_ocr_value",
    "dsl_last_ocr_at",
)


def click_approval_enabled(instance_id: str) -> bool:
    """Return whether UI click-approval gating is on for ``instance_id``.

    Default is **enabled** when the Redis key is missing (opt-out via explicit ``0`` /
    ``false`` / ``no`` / ``off``).
    """
    enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
    raw = str(_redis().get(enabled_key) or "").strip().lower()
    if not raw:
        return True
    return raw not in _CLICK_APPROVAL_DISABLED


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
    - UI writes decision to the request-specific ``response_key`` from the payload,
      then may delete ``current`` immediately so the approvals page clears preview;
      this path must still honor approve (poll ``response_key`` before inferring reject).
    """
    if not click_approval_enabled(instance_id):
        return True, None

    hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
    if not _redis().get(hb_key):
        # Approval always required — wait until the approvals page is opened.
        logger.info(
            "Click approval: page not open, waiting for operator to open it (%s)", instance_id
        )
        while not _redis().get(hb_key):
            time.sleep(_APPROVAL_POLL_SECONDS)
        logger.info("Click approval: page opened — proceeding (%s)", instance_id)

    current_key = f"wos:ui:click_approval:current:{instance_id}"

    req_id = f"adb:{instance_id}:{uuid.uuid4().hex[:12]}"
    resp_key = f"wos:ui:click_approval:response:{req_id}"

    # Attach context for debugging ("who" + "why").
    ctx: dict[str, object] = {}
    payload_type = ""
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "").strip().lower()
    try:
        inst_state_key = f"wos:instance:{instance_id}:state"
        raw = _redis().hgetall(inst_state_key)
        if raw:
            # ``current_task_region`` is the task-level region (set by the worker once
            # per task item). For ``set_node`` it is irrelevant — that step only
            # updates the FSM ``current_screen`` and never taps a region. Including
            # the stale value here would make the approvals UI draw a misleading
            # region overlay carried over from the previous step.
            task_region = (raw.get("current_task_region") or "").strip()
            if payload_type == "set_node":
                task_region = ""
            ctx = {
                "current_screen": (raw.get("current_screen") or "").strip(),
                "current_task_player": (raw.get("current_task_player") or "").strip(),
                "current_task_region": task_region,
                "current_task_threshold": (raw.get("current_task_threshold") or "").strip(),
                "current_task_score": (raw.get("current_task_score") or "").strip(),
                # YAML scenario key while a `DslScenarioTask` is running.
                "scenario": (raw.get("current_scenario") or "").strip(),
            }
            for audit_k in _DSL_APPROVAL_AUDIT_KEYS:
                ctx[audit_k] = (raw.get(audit_k) or "").strip()
            if not ctx["current_task_threshold"]:
                fb = (raw.get("last_overlay_match_threshold") or "").strip()
                if fb:
                    ctx["current_task_threshold"] = fb
            if not ctx["current_task_score"]:
                fb = (raw.get("last_overlay_match_score") or "").strip()
                if fb:
                    ctx["current_task_score"] = fb
    except Exception:
        ctx = {}

    # Fixed-coordinate taps may pass ``region`` on the payload — mirror into ``context``
    # so the approvals page shows a label even when Redis ``current_task_region`` is still empty.
    ar_hint = ""
    if isinstance(payload, dict):
        ar_hint = str(payload.get("region") or "").strip()
    if ar_hint:
        ctx = dict(ctx)
        ctx["approval_region"] = ar_hint

    p = dict(payload)
    default_source: dict[str, object] = {
        "component": "actions.tap.AdbController",
        "note": "ADB input request (approval mode enabled)",
    }
    incoming_src = p.pop("source", None)
    if isinstance(incoming_src, dict):
        merged = dict(default_source)
        merged.update(incoming_src)
        default_source = merged
    p.update(
        {
            "request_id": req_id,
            "instance_id": instance_id,
            "created_at": time.time(),
            "status": "waiting",
            "response_key": resp_key,
            "source": default_source,
            "context": ctx,
        }
    )

    _redis().delete(resp_key)
    started_at = time.time()
    # Phase 1: try to publish the request into the per-instance "current" slot.
    # ``nx=True`` so we never overwrite an in-flight approval for this instance.
    # This is bounded ONLY by ``_APPROVAL_PUBLISH_WAIT_SECONDS`` because it is
    # not waiting on the operator — only on the previous request to clear.
    publish_deadline = started_at + _APPROVAL_PUBLISH_WAIT_SECONDS
    while time.time() < publish_deadline:
        if _redis().set(
            current_key,
            json.dumps(p),
            ex=_APPROVAL_CURRENT_TTL_SECONDS,
            nx=True,
        ):
            break
        time.sleep(_APPROVAL_POLL_SECONDS)
    else:
        logger.info("ADB input blocked: approval slot busy for %s", instance_id)
        return False, None

    # Phase 2: wait for an operator decision. There is NO wall-clock timeout
    # AND NO heartbeat-loss abort — the decision is always the operator's.
    # The loop only exits when:
    #   - ``response_key`` is set to "approve" / "reject" by the UI;
    #   - a foreign request_id has taken over the slot (treated as rejected,
    #     since the slot can only be reused by another request after this
    #     ``current`` key has been explicitly cleared or has expired).
    #
    # The UI deletes ``current`` immediately after writing the response so the
    # preview clears; we therefore check ``response_key`` BEFORE inferring
    # "reject" from a foreign / missing ``current`` payload.
    decision: str | None = None
    abort_reason: str = ""
    while True:
        raw_resp = _redis().get(resp_key)
        if raw_resp:
            decision = str(raw_resp).strip().lower()
            break
        try:
            raw_cur = _redis().get(current_key)
            if raw_cur and json.loads(raw_cur).get("request_id") != req_id:
                decision = "reject"
                abort_reason = "foreign_request"
                break
        except Exception:
            logger.debug("Failed to read current approval request", exc_info=True)

        # Refresh TTL unconditionally so the request never silently expires —
        # we are committed to waiting for an operator decision, however long.
        try:
            _redis().expire(current_key, _APPROVAL_CURRENT_TTL_SECONDS)
        except Exception:
            logger.debug("Failed to refresh current_key TTL", exc_info=True)

        time.sleep(_APPROVAL_POLL_SECONDS)

    if decision in {"approve", "reject"}:
        # Persist decision time on the current payload for UI/debug.
        try:
            raw_cur = _redis().get(current_key)
            if raw_cur:
                doc = json.loads(raw_cur)
                if doc.get("request_id") == req_id:
                    doc["decision"] = decision
                    doc["approved_at"] = time.time() if decision == "approve" else None
                    doc["rejected_at"] = time.time() if decision == "reject" else None
                    doc["status"] = "approved" if decision == "approve" else "rejected"
                    _redis().set(
                        current_key,
                        json.dumps(doc),
                        ex=_APPROVAL_CURRENT_TTL_SECONDS,
                    )
        except Exception:
            logger.debug("Failed to mark decision timestamps", exc_info=True)

    # On reject/timeout, clear slot so the bot can proceed.
    if decision != "approve":
        try:
            raw_cur = _redis().get(current_key)
            if raw_cur and json.loads(raw_cur).get("request_id") == req_id:
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

    def __init__(
        self,
        instance_id: str,
        device_serial: str,
        *,
        adb_bin: str = DEFAULT_ADB_BIN,
    ) -> None:
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
        """True if the game process is alive and is the resumed foreground activity."""
        # Fast check: is the process even alive?
        pid = self._shell("pidof", _GAME_PACKAGE, timeout=5.0)
        if not pid.strip():
            logger.debug("is_game_foreground: no PID for %s — process dead", _GAME_PACKAGE)
            return False

        # Foreground check: dumpsys activity stack
        out = self._shell("dumpsys", "activity", "activities", timeout=10.0)
        markers = ("topResumedActivity=", "ResumedActivity:", "mResumedActivity:")
        for line in out.splitlines():
            if _GAME_PACKAGE not in line:
                continue
            s = line.strip()
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

    def tap(self, point: Point, *, approval_region: str | None = None) -> bool:
        """Tap center of Point with ±2 px jitter.

        ``approval_region``: logical label for click-approval UI when this tap is not tied to
        ``area.json`` (e.g. DSL helper taps); shown as ``region`` on the request payload.
        """
        x = _jitter(point.x, 2)
        y = _jitter(point.y, 2)
        ap: dict[str, object] = {
            "type": "tap",
            "x": int(x),
            "y": int(y),
            "serial": self._serial,
        }
        ar = str(approval_region or "").strip()
        if ar:
            ap["region"] = ar
        ok, req_id = _require_approval(self._instance_id, ap)
        if not ok:
            logger.info("ADB tap blocked (no approval): %s (%d,%d)", self._instance_id, x, y)
            return False
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
                # Response key is per request; cleanup if present.
                # (UI also expires it; this is best-effort.)
                # We don't have it here reliably, so clear any response for this request id.
                _redis().delete(f"wos:ui:click_approval:response:{req_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys after tap", exc_info=True)
        return True

    def swipe(
        self,
        start: Point,
        end: Point,
        duration: timedelta = timedelta(milliseconds=300),
    ) -> bool:
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
            return False
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
                _redis().delete(f"wos:ui:click_approval:response:{req_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys after swipe", exc_info=True)
        return True

    def swipe_direction(
        self,
        direction: str,
        delta: int,
        duration: timedelta = timedelta(milliseconds=300),
    ) -> bool:
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
        return self.swipe(start, end, duration)

    def long_tap(self, point: Point, duration: timedelta = timedelta(milliseconds=800)) -> bool:
        """Long-press via swipe with same start/end coords."""
        return self.swipe(point, point, duration)

    def type_text(self, text: str) -> bool:
        escaped = text.replace(" ", "%s").replace("'", "\\'")
        ok, req_id = _require_approval(
            self._instance_id,
            {"type": "type_text", "text": text, "serial": self._serial},
        )
        if not ok:
            logger.info("ADB type_text blocked (no approval): %s", self._instance_id)
            return False
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
                _redis().delete(f"wos:ui:click_approval:response:{req_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys after type_text", exc_info=True)
        return True

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

    def _shell(self, *args: str, timeout: float = 15.0) -> str:
        try:
            result = subprocess.run(
                [self._adb_exe, "-s", self._serial, "shell"] + list(args),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            logger.warning("ADB shell %s timed out after %.1fs", args, timeout)
            return ""
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

    def tap(self, instance_id: str, point: Point, *, approval_region: str | None = None) -> bool:
        return self._controller(instance_id).tap(point, approval_region=approval_region)

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        """Emulator framebuffer size from ``adb shell wm size`` (tap coordinate space)."""
        return self._controller(instance_id).get_screen_resolution()

    def swipe(
        self,
        instance_id: str,
        start: Point,
        end: Point,
        duration_ms: int = 300,
    ) -> bool:
        return self._controller(instance_id).swipe(start, end, timedelta(milliseconds=duration_ms))

    def swipe_direction(
        self, instance_id: str, direction: str, delta: int, duration_ms: int = 300
    ) -> bool:
        return self._controller(instance_id).swipe_direction(
            direction, delta, timedelta(milliseconds=duration_ms)
        )

    def long_tap(self, instance_id: str, point: Point, duration_ms: int = 800) -> bool:
        return self._controller(instance_id).long_tap(point, timedelta(milliseconds=duration_ms))

    def back(self, instance_id: str) -> None:
        logger.debug("BotActions.back(%s): no-op (phone BACK not allowed)", instance_id)

    def home(self, instance_id: str) -> None:
        logger.debug("BotActions.home(%s): no-op (phone HOME not allowed)", instance_id)

    def type_text(self, instance_id: str, text: str) -> bool:
        return self._controller(instance_id).type_text(text)

    def restart_application(self, instance_id: str) -> None:
        self._controller(instance_id).restart_application()

    def ensure_game_foreground(self, instance_id: str) -> None:
        self._controller(instance_id).ensure_game_foreground()

    def is_game_foreground(self, instance_id: str) -> bool:
        """True if ``adb dumpsys activity`` reports Whiteout as resumed top activity."""
        return self._controller(instance_id).is_game_foreground()
