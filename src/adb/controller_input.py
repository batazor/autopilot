"""Touch/keyboard input dispatch for :class:`adb.controller.AdbController`."""
from __future__ import annotations

import logging
import random
import shlex
import subprocess
import time
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING

from adb.approvals import (
    _consume_skip,
    _require_approval,
    click_approval_enabled,
)
from adb.controller_types import (
    _clamp,
    _jittered_point,
    _tap_offset_spread,
)
from layout.types import Point

if TYPE_CHECKING:
    from collections.abc import Callable

    from adb.scrcpy import ScrcpyClient

if TYPE_CHECKING:
    from adb._controller_host import _ControllerHost as _Base
else:
    _Base = object

logger = logging.getLogger(__name__)

_SWIPE_MIN_DURATION_MS = 900
_SWIPE_SETTLE_SECONDS = 0.25
_SWIPE_DURATION_JITTER_PCT = 0.15
_SWIPE_CURVE_OFFSET_PCT_MIN = 0.03
_SWIPE_CURVE_OFFSET_PCT_MAX = 0.08
_SWIPE_CURVE_SEGMENTS = 4
_TAP_HOLD_MS_MIN = 35
_TAP_HOLD_MS_MAX = 90
_TAP_MICRO_MOVE_PROBABILITY = 0.28
_SWIPE_ENDPOINT_JITTER_PCT = 0.018
_SWIPE_ENDPOINT_JITTER_MAX_PX = 24
_LONG_SWIPE_OVERSHOOT_MIN_PX = 260


class AdbInputMixin(_Base):
    """Tap/swipe/back/type dispatch — all with jitter to simulate natural fingers."""

    # ------------------------------------------------------------------
    # Touch input — all with jitter to simulate natural fingers
    # ------------------------------------------------------------------

    def _get_scrcpy(self) -> ScrcpyClient | None:
        """Resolve the shared scrcpy client when ``input_backend == 'scrcpy'``.

        Returns ``None`` only when this device is NOT configured for scrcpy
        (``input_backend != "scrcpy"``) — that's the legitimate "use ADB"
        path because the operator never asked for scrcpy.

        Raises ``RuntimeError`` when scrcpy IS configured but unavailable.
        Silent degradation to ``adb shell input`` would mask the real fault
        (client never started, server died, USB unplugged) behind a slower
        bot, exactly the masking behaviour we removed by design.
        """
        if self._input_backend != "scrcpy":
            return None
        getter = self._scrcpy_client_getter
        if getter is None:
            msg = (
                f"input_backend=scrcpy on {self._serial} but no scrcpy "
                f"client getter wired — refusing to silently use adb"
            )
            raise RuntimeError(msg)
        client = getter()  # propagate getter failures verbatim
        if client is None or not client.is_alive():
            msg = (
                f"scrcpy is the configured input backend for {self._serial} "
                f"but the client is unavailable — refusing to silently use adb"
            )
            raise RuntimeError(msg)
        return client

    def _emit_tap(self, x: int, y: int) -> None:
        """Send a single tap via the configured backend. Raises on failure."""
        sc = self._get_scrcpy()
        if sc is not None:
            sc.tap(x, y)
            return
        hold_ms = random.randint(_TAP_HOLD_MS_MIN, _TAP_HOLD_MS_MAX)
        if self._detect_motionevent_support():
            self._shell("input", "motionevent", "DOWN", str(x), str(y))
            if random.random() < _TAP_MICRO_MOVE_PROBABILITY:
                time.sleep(hold_ms / 1000.0 * random.uniform(0.35, 0.7))
                mx = x + random.choice((-1, 1)) * random.randint(1, 2)
                my = y + random.choice((-1, 1)) * random.randint(1, 2)
                w, h = self.get_screen_resolution()
                mx = _clamp(mx, 0, max(0, w - 1))
                my = _clamp(my, 0, max(0, h - 1))
                self._shell("input", "motionevent", "MOVE", str(mx), str(my))
                time.sleep(hold_ms / 1000.0 * random.uniform(0.2, 0.45))
            else:
                time.sleep(hold_ms / 1000.0)
            self._shell("input", "motionevent", "UP", str(x), str(y))
            return
        self._shell(
            "input", "touchscreen", "swipe",
            str(x), str(y), str(x), str(y), str(hold_ms),
        )

    def _emit_long_press(self, x: int, y: int, ms: int) -> None:
        sc = self._get_scrcpy()
        if sc is not None:
            sc.long_press(x, y, duration_ms=ms)
            return
        # adb path: zero-distance `input swipe` is the standard long-press idiom.
        self._shell("input", "swipe", str(x), str(y), str(x), str(y), str(ms))

    def _emit_swipe_straight(
        self, x1: int, y1: int, x2: int, y2: int, ms: int,
    ) -> None:
        """Straight swipe via the configured backend. Raises on failure."""
        sc = self._get_scrcpy()
        if sc is not None:
            sc.swipe(x1, y1, x2, y2, duration_ms=ms)
            return
        self._shell(
            "input", "touchscreen", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(ms),
        )

    def tap(
        self,
        point: Point,
        *,
        preview_point: Point | None = None,
        approval_region: str | None = None,
        approval_source: str | None = None,
        approval_context: dict[str, object] | None = None,
        require_approval: bool = True,
        revalidate: Callable[[], bool] | None = None,
        hold_ms: int = 0,
    ) -> bool:
        """Tap center of Point with ±2 px jitter.

        ``approval_region``: logical label for click-approval UI when this tap is not tied to
        ``area.json`` (e.g. DSL helper taps); shown as ``region`` on the request payload.

        ``revalidate``: optional callable invoked after the operator approves
        but before the ADB tap fires. When it returns ``False`` the tap is
        cancelled (no ADB shell call, ``False`` returned). Used by the
        ``template_icon`` navigator to re-verify the icon is still under the
        recorded coordinates — without it, a minutes-old approval can fire a
        tap on a now-empty spot after a rotating event icon has cycled away.

        ``hold_ms``: when > 0, dispatch as a long-press (zero-distance swipe
        held for ``hold_ms``) instead of an instantaneous tap. Some game
        overlays (``tap anywhere to exit``-style dismiss prompts) debounce
        out the ~0 ms DOWN→UP sequence ``input tap`` emits and only respond
        to a touch that lingers; per-region ``tap_hold_ms`` in ``area.json``
        opts those targets into long-press here.
        """
        tap_point = _jittered_point(
            point,
            spread=_tap_offset_spread(),
            bounds=self.get_screen_resolution(),
        )
        x, y = tap_point.x, tap_point.y
        preview = preview_point or Point(x, y)
        hold = max(0, int(hold_ms))
        ap: dict[str, object] = {
            "type": "tap",
            "x": int(preview.x),
            "y": int(preview.y),
            "serial": self._serial,
        }
        if hold > 0:
            ap["hold_ms"] = hold
        ar = str(approval_region or "").strip()
        if ar:
            ap["region"] = ar
        src = str(approval_source or "").strip()
        if src:
            ap["approval_source"] = src
        if approval_context:
            ap["approval_context"] = dict(approval_context)
        if require_approval and click_approval_enabled(self._instance_id) and src != "navigation":
            self._attach_approval_preview(ap)
            ap["_preview_capturer"] = self._attach_approval_preview
        ok, req_id = (
            _require_approval(self._instance_id, ap)
            if require_approval
            else (True, None)
        )
        if not ok:
            logger.info("ADB tap blocked (no approval): %s (%d,%d)", self._instance_id, x, y)
            return False
        if _consume_skip(req_id):
            logger.info(
                "ADB tap skipped by operator: %s (%d,%d)", self._instance_id, x, y
            )
            self._refresh_rolling_preview()
            return True
        if revalidate is not None:
            try:
                still_valid = bool(revalidate())
            except Exception:
                logger.warning(
                    "ADB tap revalidate hook raised on %s (%d,%d) — treating as no-match",
                    self._instance_id, x, y, exc_info=True,
                )
                still_valid = False
            if not still_valid:
                logger.info(
                    "ADB tap cancelled by revalidate (stale target): %s (%d,%d)",
                    self._instance_id, x, y,
                )
                # Clean up approval slot so the next request can publish; we
                # intentionally do NOT consume_skip — the operator's approve
                # was legit, the underlying target just moved.
                with suppress(Exception):
                    self._refresh_rolling_preview()
                return False
        with self._approval_execution(req_id):
            if hold > 0:
                self._emit_long_press(x, y, hold)
                logger.debug(
                    "Long-tap (%d, %d) hold=%dms on %s", x, y, hold, self._serial
                )
            else:
                self._emit_tap(x, y)
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
        overshoot: tuple[float, float] | None = None
        if length >= _LONG_SWIPE_OVERSHOOT_MIN_PX and random.random() < 0.35:
            overshoot_px = min(42.0, max(8.0, length * random.uniform(0.025, 0.07)))
            overshoot = (
                x2 + dx / length * overshoot_px,
                y2 + dy / length * overshoot_px,
            )
            n += random.randint(1, 2)
        for i in range(n + 1):
            t = i / n
            target_x = x2
            target_y = y2
            if overshoot is not None and i >= n - 1:
                target_x, target_y = overshoot
            bx = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * target_x
            by = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * target_y
            # ±1 px per-point jitter so even with the same endpoints the
            # intermediate trail isn't reproducible across runs.
            bx += random.randint(-1, 1)
            by += random.randint(-1, 1)
            pts.append((int(round(bx)), int(round(by))))
        if pts:
            pts[-1] = (int(x2), int(y2))
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
        *,
        preview_start: Point | None = None,
        preview_end: Point | None = None,
    ) -> bool:
        """Swipe with ±2 px endpoint jitter, ±15 % duration jitter, and a
        slight Bezier curve through a perpendicular-offset midpoint.
        """
        # Important: for "long press" we call swipe(start=end). In that case we must
        # keep start/end identical; otherwise independent jitter turns it into a
        # tiny swipe (1–2 px) which many UIs ignore as a press/hold.
        bounds = self.get_screen_resolution()
        if int(start.x) == int(end.x) and int(start.y) == int(end.y):
            p = _jittered_point(start, spread=_tap_offset_spread(), bounds=bounds)
            x = p.x
            y = p.y
            x1 = x2 = x
            y1 = y2 = y
        else:
            dx = int(end.x) - int(start.x)
            dy = int(end.y) - int(start.y)
            length = (dx * dx + dy * dy) ** 0.5
            spread = _clamp(
                int(round(length * _SWIPE_ENDPOINT_JITTER_PCT)),
                2,
                _SWIPE_ENDPOINT_JITTER_MAX_PX,
            )
            start_j = _jittered_point(start, spread=spread, bounds=bounds)
            end_j = _jittered_point(end, spread=spread, bounds=bounds)
            x1, y1 = start_j.x, start_j.y
            x2, y2 = end_j.x, end_j.y
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
        p_start = preview_start or Point(x1, y1)
        p_end = preview_end or Point(x2, y2)
        swipe_payload: dict[str, object] = {
            "type": "swipe",
            "x1": int(p_start.x),
            "y1": int(p_start.y),
            "x2": int(p_end.x),
            "y2": int(p_end.y),
            "ms": int(ms),
            "serial": self._serial,
        }
        # Same coordinates → long press; approvals UI expects x/y like ``tap`` for crosshair.
        if is_long_press:
            swipe_payload["gesture"] = "long_press"
            swipe_payload["x"] = int(p_start.x)
            swipe_payload["y"] = int(p_start.y)
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
            if is_long_press:
                self._emit_long_press(x1, y1, ms)
            elif self._input_backend == "scrcpy":
                self._emit_swipe_straight(x1, y1, x2, y2, ms)
            elif not self._dispatch_curved_swipe(x1, y1, x2, y2, ms):
                # motionevent unsupported or shell failed — straight fallback.
                self._emit_swipe_straight(x1, y1, x2, y2, ms)
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
        """Swipe left/right/up/down by delta pixels from a screen-aware lane."""
        w, h = self.get_screen_resolution()
        margin = 24

        def clamp_x(x: int) -> int:
            return _clamp(x, margin, max(margin, w - margin))

        def clamp_y(y: int) -> int:
            return _clamp(y, margin, max(margin, h - margin))

        match direction.lower():
            case "left":
                y = clamp_y(int(round(random.uniform(0.42, 0.62) * h)))
                x = clamp_x(int(round(random.uniform(0.58, 0.76) * w)))
                start, end = Point(x, y), Point(clamp_x(x - delta), y)
            case "right":
                y = clamp_y(int(round(random.uniform(0.42, 0.62) * h)))
                x = clamp_x(int(round(random.uniform(0.24, 0.42) * w)))
                start, end = Point(x, y), Point(clamp_x(x + delta), y)
            case "up":
                x = clamp_x(int(round(random.uniform(0.38, 0.62) * w)))
                y = clamp_y(int(round(random.uniform(0.60, 0.76) * h)))
                start, end = Point(x, y), Point(x, clamp_y(y - delta))
            case "down":
                x = clamp_x(int(round(random.uniform(0.38, 0.62) * w)))
                y = clamp_y(int(round(random.uniform(0.30, 0.46) * h)))
                start, end = Point(x, y), Point(x, clamp_y(y + delta))
            case _:
                msg = f"Unknown swipe direction: {direction!r}"
                raise ValueError(msg)
        return self.swipe(start, end, duration)

    def long_tap(self, point: Point, duration: timedelta = timedelta(milliseconds=800)) -> bool:
        """Long-press via swipe with same start/end coords."""
        return self.swipe(point, point, duration)

    def system_back(self) -> bool:
        payload = self._approval_payload_with_preview(
            {"type": "system_back", "keycode": "KEYCODE_BACK", "serial": self._serial}
        )
        ok, req_id = _require_approval(self._instance_id, payload)
        if not ok:
            logger.info("ADB system BACK blocked (no approval): %s", self._instance_id)
            return False
        if _consume_skip(req_id):
            logger.info("ADB system BACK skipped by operator: %s", self._instance_id)
            self._refresh_rolling_preview()
            return True
        with self._approval_execution(req_id):
            self._shell("input", "keyevent", "KEYCODE_BACK")
            logger.debug("System BACK on %s", self._serial)
            self._refresh_rolling_preview()
        return True

    def type_text(self, text: str) -> bool:
        # ``adb shell ARGS`` concatenates ARGS and runs them through the
        # device-side ``sh``, so shell metacharacters (``&``, ``;``, ``|``,
        # backticks, ``$``, etc.) in ``text`` would otherwise be interpreted
        # or fail to escape correctly. ``input text`` itself can't accept
        # spaces, so we first replace them with the ``%s`` placeholder it
        # understands, then wrap the whole thing in ``shlex.quote`` so the
        # device shell hands the literal string to ``input text``.
        escaped = shlex.quote(text.replace(" ", "%s"))
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
