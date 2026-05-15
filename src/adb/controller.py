"""Low-level ADB device controller (tap, swipe, shell, app lifecycle)."""
from __future__ import annotations

import json
import logging
import os
import random
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from adb.approvals import (
    _consume_skip,
    _redis,
    _require_approval,
    click_approval_enabled,
)
from adb.screencap import (
    DEFAULT_ADB_BIN,
    MSG_ADB_NOT_FOUND,
    adb_screencap_png,
    resolve_adb_executable,
)
from config.paths import repo_root
from config.reference_naming import (
    reference_png_abs_path,
    rolling_preview_basename,
    temporal_png_abs_path,
)
from layout.types import Point

logger = logging.getLogger(__name__)

_GAME_PACKAGE = "com.gof.global"
_SWIPE_MIN_DURATION_MS = 900
_SWIPE_SETTLE_SECONDS = 0.25
_SWIPE_DURATION_JITTER_PCT = 0.15
_SWIPE_CURVE_OFFSET_PCT_MIN = 0.03
_SWIPE_CURVE_OFFSET_PCT_MAX = 0.08
_SWIPE_CURVE_SEGMENTS = 4


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
        # Probed lazily on first curved-swipe attempt. ``None`` = unknown,
        # ``True``/``False`` cached after the first ``input`` help dump.
        self._supports_motionevent: bool | None = None
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

    def tap(
        self,
        point: Point,
        *,
        approval_region: str | None = None,
        approval_source: str | None = None,
        approval_context: dict[str, object] | None = None,
    ) -> bool:
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
        src = str(approval_source or "").strip()
        if src:
            ap["approval_source"] = src
        if approval_context:
            ap["approval_context"] = dict(approval_context)
        if click_approval_enabled(self._instance_id):
            self._attach_approval_preview(ap)
        ok, req_id = _require_approval(self._instance_id, ap)
        if not ok:
            logger.info("ADB tap blocked (no approval): %s (%d,%d)", self._instance_id, x, y)
            return False
        if _consume_skip(req_id):
            logger.info(
                "ADB tap skipped by operator: %s (%d,%d)", self._instance_id, x, y
            )
            self._refresh_rolling_preview()
            return True
        with self._approval_execution(req_id):
            self._shell("input", "tap", str(x), str(y))
            logger.debug("Tap (%d, %d) on %s", x, y, self._serial)
            self._refresh_rolling_preview()
        return True

    def _detect_motionevent_support(self) -> bool:
        """One-shot probe: does this emulator's ``input`` binary expose
        ``motionevent`` (DOWN/MOVE/UP)? Added in API 26, present on most
        BlueStacks builds shipping Android 9, missing on legacy 7 images.
        """
        if self._supports_motionevent is not None:
            return self._supports_motionevent
        try:
            result = subprocess.run(
                [self._adb_exe, "-s", self._serial, "shell", "input"],
                capture_output=True,
                text=True,
                timeout=5.0,
            )
            blob = ((result.stdout or "") + (result.stderr or "")).lower()
            self._supports_motionevent = "motionevent" in blob
        except Exception:
            self._supports_motionevent = False
        logger.debug(
            "AdbController: motionevent support on %s = %s",
            self._serial, self._supports_motionevent,
        )
        return self._supports_motionevent

    def _curved_swipe_points(
        self, x1: int, y1: int, x2: int, y2: int
    ) -> list[tuple[int, int]]:
        """Sample a quadratic-Bezier swipe path with ±1 px per-point jitter.

        Control point: midpoint pushed perpendicular to the start→end line
        by a random fraction of the swipe length. Yields
        ``_SWIPE_CURVE_SEGMENTS + 1`` points (start + N intermediates + end).
        Falls back to a 2-point straight path for zero-length swipes.
        """
        dx = float(x2 - x1)
        dy = float(y2 - y1)
        length = (dx * dx + dy * dy) ** 0.5
        if length <= 0.0:
            return [(int(x1), int(y1)), (int(x2), int(y2))]
        # Unit perpendicular to the swipe direction.
        perp_x = -dy / length
        perp_y = dx / length
        offset_amount = (
            random.uniform(_SWIPE_CURVE_OFFSET_PCT_MIN, _SWIPE_CURVE_OFFSET_PCT_MAX)
            * length
            * random.choice([-1.0, 1.0])
        )
        cx = (x1 + x2) / 2.0 + perp_x * offset_amount
        cy = (y1 + y2) / 2.0 + perp_y * offset_amount
        pts: list[tuple[int, int]] = []
        n = _SWIPE_CURVE_SEGMENTS
        for i in range(n + 1):
            t = i / n
            bx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2
            by = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2
            # ±1 px per-point jitter so even with the same endpoints the
            # intermediate trail isn't reproducible across runs.
            bx += random.randint(-1, 1)
            by += random.randint(-1, 1)
            pts.append((int(round(bx)), int(round(by))))
        return pts

    def _dispatch_curved_swipe(
        self, x1: int, y1: int, x2: int, y2: int, ms: int
    ) -> bool:
        """Run the swipe as a DOWN/MOVE.../UP ``input motionevent`` chain.

        Returns ``False`` when motionevent isn't supported or any shell call
        fails — the caller falls back to a single straight
        ``input touchscreen swipe``. The touch stays held by Android's
        InputManager between events so the sequence is one continuous
        gesture, not multiple distinct swipes.
        """
        if not self._detect_motionevent_support():
            return False
        pts = self._curved_swipe_points(x1, y1, x2, y2)
        if len(pts) < 2:
            return False
        try:
            self._shell(
                "input", "motionevent", "DOWN", str(pts[0][0]), str(pts[0][1])
            )
            # Sleep gates the per-segment timing on the python side. Includes
            # the inherent ADB latency of each subsequent ``_shell`` call
            # (~80 ms) — the visible gesture ends up slightly longer than
            # ``ms`` but stays continuous because the touch isn't lifted.
            seg_sleep = max(0.005, (ms / 1000.0) / max(1, len(pts) - 1))
            for px, py in pts[1:-1]:
                time.sleep(seg_sleep)
                self._shell("input", "motionevent", "MOVE", str(px), str(py))
            time.sleep(seg_sleep)
            self._shell(
                "input", "motionevent", "UP", str(pts[-1][0]), str(pts[-1][1])
            )
        except Exception:
            logger.warning(
                "curved swipe dispatch failed on %s — falling back to straight swipe",
                self._serial,
                exc_info=True,
            )
            return False
        return True

    def swipe(
        self,
        start: Point,
        end: Point,
        duration: timedelta = timedelta(milliseconds=300),
    ) -> bool:
        """Swipe with ±2 px endpoint jitter, ±15 % duration jitter, and a
        slight Bezier curve through a perpendicular-offset midpoint.
        """
        # Important: for "long press" we call swipe(start=end). In that case we must
        # keep start/end identical; otherwise independent jitter turns it into a
        # tiny swipe (1–2 px) which many UIs ignore as a press/hold.
        if int(start.x) == int(end.x) and int(start.y) == int(end.y):
            x = _jitter(start.x, 2)
            y = _jitter(start.y, 2)
            x1 = x2 = x
            y1 = y2 = y
        else:
            x1 = _jitter(start.x, 2)
            y1 = _jitter(start.y, 2)
            x2 = _jitter(end.x, 2)
            y2 = _jitter(end.y, 2)
        ms = int(duration.total_seconds() * 1000)
        if x1 != x2 or y1 != y2:
            ms = max(ms, _SWIPE_MIN_DURATION_MS)
            # ±15 % around the (post-min) duration. ``ms`` is recomputed
            # via ``max`` so the floor still holds — jitter only widens
            # the upper edge.
            ms = max(
                _SWIPE_MIN_DURATION_MS,
                int(round(ms * random.uniform(
                    1.0 - _SWIPE_DURATION_JITTER_PCT,
                    1.0 + _SWIPE_DURATION_JITTER_PCT,
                ))),
            )
        is_long_press = x1 == x2 and y1 == y2
        swipe_payload: dict[str, object] = {
            "type": "swipe",
            "x1": int(x1),
            "y1": int(y1),
            "x2": int(x2),
            "y2": int(y2),
            "ms": int(ms),
            "serial": self._serial,
        }
        # Same coordinates → long press; approvals UI expects x/y like ``tap`` for crosshair.
        if is_long_press:
            swipe_payload["gesture"] = "long_press"
            swipe_payload["x"] = int(x1)
            swipe_payload["y"] = int(y1)
        ok, req_id = _require_approval(
            self._instance_id,
            self._approval_payload_with_preview(swipe_payload),
        )
        if not ok:
            logger.info("ADB swipe blocked (no approval): %s", self._instance_id)
            return False
        if _consume_skip(req_id):
            logger.info("ADB swipe skipped by operator: %s", self._instance_id)
            self._refresh_rolling_preview()
            return True
        with self._approval_execution(req_id):
            # Zero-distance swipe: prefer plain ``input swipe`` — ``touchscreen swipe`` often
            # fails on emulators for press-and-hold at one pixel.
            if is_long_press:
                self._shell("input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))
            elif not self._dispatch_curved_swipe(x1, y1, x2, y2, ms):
                # motionevent unsupported or shell failed — straight fallback.
                self._shell(
                    "input", "touchscreen", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)
                )
            logger.debug("Swipe (%d,%d)→(%d,%d) %dms on %s", x1, y1, x2, y2, ms, self._serial)
            time.sleep(_SWIPE_SETTLE_SECONDS)
            self._refresh_rolling_preview()
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
            self._approval_payload_with_preview(
                {"type": "type_text", "text": text, "serial": self._serial}
            ),
        )
        if not ok:
            logger.info("ADB type_text blocked (no approval): %s", self._instance_id)
            return False
        if _consume_skip(req_id):
            logger.info("ADB type_text skipped by operator: %s", self._instance_id)
            self._refresh_rolling_preview()
            return True
        with self._approval_execution(req_id):
            self._shell("input", "text", escaped)
            logger.debug("Type '%s' on %s", text, self._serial)
            self._refresh_rolling_preview()
        return True

    # ------------------------------------------------------------------
    # Screenshot via ADB
    # ------------------------------------------------------------------

    def screenshot_bytes(self) -> bytes:

        data, err = adb_screencap_png(self._adb_exe, self._serial)
        if data is None:
            raise RuntimeError(err)
        return data

    def _approval_payload_with_preview(self, payload: dict[str, object]) -> dict[str, object]:
        p = dict(payload)
        if click_approval_enabled(self._instance_id):
            self._attach_approval_preview(p)
            # Let ``_require_approval`` re-capture the preview right before
            # serialising for publish — the gap between this initial attach
            # and the actual SET can stretch into seconds (or longer) when
            # the approval slot is contended. Popped + invoked there.
            p["_preview_capturer"] = self._attach_approval_preview
        return p

    def attach_approval_preview(self, payload: dict[str, object]) -> None:
        if click_approval_enabled(self._instance_id):
            self._attach_approval_preview(payload)
            payload["_preview_capturer"] = self._attach_approval_preview

    def _write_png_bytes_atomic(self, *, path: Path, png: bytes, tmp_prefix: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=tmp_prefix, suffix=".png", dir=path.parent
        )
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            tmp.write_bytes(png)
            os.replace(tmp, path)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _capture_to_rolling_preview(self, *, tmp_prefix: str) -> tuple[Path, Path] | None:
        """Capture a fresh frame and atomically overwrite ``current_state.png``.

        Returns ``(absolute_path, repo_root)`` on success, ``None`` on failure.
        Used by both pre-approval (`_attach_approval_preview`) and post-action
        (`_refresh_rolling_preview`) writers.
        """
        try:
            png = self.screenshot_bytes()
            root = repo_root()
            path = reference_png_abs_path(
                root,
                rolling_preview_basename(self._instance_id),
                self._instance_id,
            )
            self._write_png_bytes_atomic(path=path, png=png, tmp_prefix=tmp_prefix)
            return path, root
        except Exception:
            return None

    def _attach_approval_preview(self, payload: dict[str, object]) -> None:
        """Capture pre-approval frame, attach a stable request snapshot to payload."""
        try:
            png = self.screenshot_bytes()
            root = repo_root()
            rolling_path = reference_png_abs_path(
                root,
                rolling_preview_basename(self._instance_id),
                self._instance_id,
            )
            approval_path = temporal_png_abs_path(
                root,
                f"{self._instance_id}_approval_current",
            )
            self._write_png_bytes_atomic(
                path=rolling_path,
                png=png,
                tmp_prefix=".approval-live-",
            )
            self._write_png_bytes_atomic(
                path=approval_path,
                png=png,
                tmp_prefix=".approval-snapshot-",
            )
        except Exception:
            logger.debug(
                "Failed to capture approval preview for %s", self._instance_id, exc_info=True
            )
            return
        rel = approval_path.relative_to(root / "references")
        payload["preview_png_rel"] = rel.as_posix()
        payload["preview_captured_at"] = time.time()

    def _refresh_rolling_preview(self) -> None:
        """Capture post-action frame so the rolling preview reflects the new state.

        Only runs when approval mode is enabled — outside approval mode the
        rolling timer loop in instance_worker is the only writer.
        """
        if not click_approval_enabled(self._instance_id):
            return
        if self._capture_to_rolling_preview(tmp_prefix=".post-action-") is None:
            logger.debug(
                "Failed to refresh rolling preview for %s", self._instance_id, exc_info=True
            )

    @contextmanager
    def _approval_execution(self, req_id: str | None) -> Iterator[None]:
        """Mark the approval slot as ``executing`` for the duration of the action.

        Wraps the actual ADB ``input`` shell call so the approvals UI can
        distinguish "waiting for action to complete" from "still waiting for
        operator decision".  Cleans up the slot and per-request response key
        after the action returns (or raises).  No-op when approval is disabled
        (``req_id is None``).
        """
        if req_id is None:
            yield
            return
        current_key = f"wos:ui:click_approval:current:{self._instance_id}"
        try:
            raw = _redis().get(current_key)
            if raw:
                doc = json.loads(raw)
                doc["executed_at"] = time.time()
                doc["status"] = "executing"
                _redis().set(current_key, json.dumps(doc), ex=120)
        except Exception:
            logger.debug("Failed to mark executed_at", exc_info=True)
        try:
            yield
        finally:
            try:
                _redis().delete(current_key)
                _redis().delete(f"wos:ui:click_approval:response:{req_id}")
            except Exception:
                logger.debug("Failed to cleanup approval keys", exc_info=True)

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


