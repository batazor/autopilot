"""Instance-aware facade: instance_id → ADB serial → AdbController."""
from __future__ import annotations

import contextlib
import logging
import random
import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from adb.controller import AdbController, ProcessDetection
from adb.frame_normalize import (
    GAME_FRAME_SIZE,
    normalize_adb_frame_bgr_with_transform,
    normalized_point_to_source_point,
)
from adb.quartz_screencap import quartz_screencap_bgr
from adb.scrcpy import DEFAULT_PORT_BASE as _SCRCPY_PORT_BASE
from adb.scrcpy import ScrcpyClient, close_scrcpy_client, get_or_create_scrcpy_client
from adb.screencap import DEFAULT_ADB_BIN, adb_screencap_bgr_with_transform, resolve_adb_executable
from adb.serial import is_emulator_adb_serial
from layout.types import Point
from worker import frame_bus

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

    from adb.frame_normalize import FrameNormalizeTransform
    from config.loader import InstanceConfig, Settings
logger = logging.getLogger(__name__)

_FIRST_FRAME_TIMEOUT_S = 30.0
_NEXT_FRAME_TIMEOUT_S = 3.0
# scrcpy reader thread caches the latest H264 frame (~30 FPS on physical
# devices), so a "next frame" event arrives every ~33ms. Using a 3s timeout
# here would mean post-tap captures wait for the rolling loop's 2s interval
# instead of grabbing the already-cached frame. 200ms gives generous slack
# for slow encoders without serialising taps behind rolling ticks.
_SCRCPY_NEXT_FRAME_TIMEOUT_S = 0.2
# After a touch-producing action, scrcpy can decode one or two old-screen
# frames before the game processes the input and redraws. The next analyzer
# capture must cross this boundary or it may match and click the same banner
# twice.
_POST_ACTION_FRAME_SETTLE_S = 0.25
_DISPLAY_SETTLE_AFTER_WM_S = 5.0
_SWIPE_EDGE_MARGIN_PX = 24


def _directional_swipe_points(
    frame_w: int,
    frame_h: int,
    direction: str,
    delta: int,
) -> tuple[Point, Point]:
    """Pick a plausible swipe lane instead of always using the exact center."""

    margin = _SWIPE_EDGE_MARGIN_PX
    direction = direction.lower()
    delta = max(0, int(delta))

    def clamp_x(x: int) -> int:
        return max(margin, min(frame_w - margin, x))

    def clamp_y(y: int) -> int:
        return max(margin, min(frame_h - margin, y))

    if direction == "up":
        x = clamp_x(int(round(random.uniform(0.38, 0.62) * frame_w)))
        y1 = clamp_y(int(round(random.uniform(0.60, 0.76) * frame_h)))
        return Point(x, y1), Point(x, clamp_y(y1 - delta))
    if direction == "down":
        x = clamp_x(int(round(random.uniform(0.38, 0.62) * frame_w)))
        y1 = clamp_y(int(round(random.uniform(0.30, 0.46) * frame_h)))
        return Point(x, y1), Point(x, clamp_y(y1 + delta))
    if direction == "left":
        y = clamp_y(int(round(random.uniform(0.42, 0.62) * frame_h)))
        x1 = clamp_x(int(round(random.uniform(0.58, 0.76) * frame_w)))
        return Point(x1, y), Point(clamp_x(x1 - delta), y)
    if direction == "right":
        y = clamp_y(int(round(random.uniform(0.42, 0.62) * frame_h)))
        x1 = clamp_x(int(round(random.uniform(0.24, 0.42) * frame_w)))
        return Point(x1, y), Point(clamp_x(x1 + delta), y)
    msg = f"Unknown swipe direction: {direction!r}"
    raise ValueError(msg)


class BotActions:
    """Instance-aware facade: resolves instance_id → ADB serial → AdbController."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._controllers: dict[str, AdbController] = {}
        # Per-instance "last frame" cache.  ``capture_screen_bgr_cached`` returns
        # this until the next state-changing action (tap/swipe/long_tap/type/…)
        # invalidates it.  The plain ``capture_screen_bgr`` stays fresh-only so
        # existing callers that expect a new ADB screencap still get one.
        # Scope is a single ``BotActions`` instance (one task execution), so the
        # cache is dropped when the task ends — no leak across scenarios.
        # Each entry is ``(monotonic_ts, frame, transform)``; callers that pass
        # ``max_age_ms`` to ``capture_screen_bgr_cached`` use the timestamp to
        # opt out of stale frames (OCR, timer reads). ``None`` keeps the
        # tap-invalidation-only behavior used by ``match`` / ``while_match``.
        self._frame_cache: dict[
            str,
            tuple[float, np.ndarray, FrameNormalizeTransform | None],
        ] = {}
        self._frame_cache_lock = threading.Lock()
        self._await_next_frame: dict[str, float] = {}
        self._FIRST_FRAME_TIMEOUT_S = _FIRST_FRAME_TIMEOUT_S
        self._NEXT_FRAME_TIMEOUT_S = _NEXT_FRAME_TIMEOUT_S
        # scrcpy (Genymobile) clients: one server process per instance, shared
        # between screenshot (here) and input (AdbController). Registry is
        # module-level — AdbController fetches the same client by serial.
        #
        # NOTE: there is **no** silent ADB fallback when scrcpy is the
        # configured backend. If start() or capture fails, the exception
        # propagates so the operator sees the real reason (device offline,
        # JAR push denied, server crash, …) instead of a slow-and-mysterious
        # bot running on ``adb exec-out screencap``. Fix scrcpy → restart.
        self._scrcpy_lock = threading.Lock()

    def _controller(self, instance_id: str) -> AdbController:
        if instance_id not in self._controllers:
            inst = self._get_instance(instance_id)
            serial = inst.bluestacks_window_title
            self._controllers[instance_id] = AdbController(
                instance_id,
                serial,
                adb_bin=self._adb_bin(),
                input_backend=inst.input_backend,
                # When input_backend=="scrcpy" the controller pulls the same
                # client we use for screenshots (one server process per device).
                scrcpy_client_getter=lambda iid=instance_id: self._get_scrcpy_client(iid),
            )
        return self._controllers[instance_id]

    def _get_instance(self, instance_id: str) -> InstanceConfig:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return inst
        msg = f"Unknown instance_id: {instance_id!r}"
        raise ValueError(msg)

    def _get_serial(self, instance_id: str) -> str:
        return self._get_instance(instance_id).bluestacks_window_title  # ADB serial for BlueStacks

    def _get_game(self, instance_id: str) -> str:
        """Resolve the game id for ``instance_id`` (the configured game on its device)."""
        return self._get_instance(instance_id).game

    def apply_device_display(self, instance_id: str) -> bool:
        """Apply merged worker + per-device display profile (wm, brightness, …).

        Returns True iff ``wm size`` / ``wm density`` actually changed on this call.
        Brightness / heads-up / keep-screen-on don't count: the game observes
        them without a restart.
        """
        from adb.device_display import apply_device_display_config
        from config.device_display import merge_device_display

        merged = merge_device_display(
            self._settings.worker.device_display,
            self._get_instance(instance_id).display,
        )
        if merged is None:
            return False
        return apply_device_display_config(
            self._controller(instance_id),
            serial=self._get_serial(instance_id),
            config=merged,
        )

    def apply_display_then_launch_game(
        self,
        instance_id: str,
        *,
        require_approval: bool = True,
        settle_s: float = _DISPLAY_SETTLE_AFTER_WM_S,
    ) -> None:
        """Apply wm/density (and related settings), then start Whiteout.

        Restart the game only when ``wm size`` / ``wm density`` actually changed
        on this boot — otherwise an already-running app is left alone (no
        force-stop, no relaunch). Without this check, every worker boot
        reset the in-game session even when the display profile was already
        applied from a prior run.
        """
        display_changed = self.apply_device_display(instance_id)
        if settle_s > 0 and display_changed:
            time.sleep(settle_s)
        ctrl = self._controller(instance_id)
        game = self._get_game(instance_id)
        is_fg = ctrl.is_game_foreground(game)
        if is_fg and not display_changed:
            logger.info(
                "Game %s already running with matching display on %s — no restart",
                game, instance_id,
            )
            return
        if is_fg:
            logger.info(
                "Restarting game %s after display profile change on %s",
                game, instance_id,
            )
            ctrl.restart_application(game)
        else:
            logger.info(
                "Launching game %s on %s", game, instance_id,
            )
            ctrl.ensure_game_foreground(game, require_approval=require_approval)

    def _adb_bin(self) -> str:
        # Resolve the adb path eagerly so downstream consumers (scrcpy,
        # screencap, controller) all see the same absolute path. Without
        # the resolve, scrcpy.py runs ``DEFAULT_ADB_BIN`` literally and
        # fails on machines where adb isn't at the Apple-Silicon default
        # ``/opt/homebrew/bin/adb`` (e.g. Intel Homebrew at /usr/local).
        pref = (self._settings.worker.adb_executable or "").strip()
        resolved = resolve_adb_executable(pref or DEFAULT_ADB_BIN)
        return resolved or (pref or DEFAULT_ADB_BIN)

    def _to_adb_point(self, instance_id: str, point: Point) -> Point:
        """Map bot-frame coordinates (720x1280) into the device touch space."""

        with self._frame_cache_lock:
            cached = self._frame_cache.get(instance_id)
        transform = cached[2] if cached is not None else None
        if transform is None:
            latest = frame_bus.latest_snapshot(instance_id)
            if latest is not None:
                transform = latest.transform
        if transform is not None:
            return transform.normalized_to_source_point(point)

        return normalized_point_to_source_point(
            point,
            self._controller(instance_id).get_screen_resolution(),
            target_size=GAME_FRAME_SIZE,
        )

    def invalidate_frame_cache(self, instance_id: str | None = None) -> None:
        """Drop the cached framebuffer for ``instance_id`` (or all instances)."""
        now = time.monotonic()
        with self._frame_cache_lock:
            if instance_id is None:
                self._frame_cache.clear()
                self._await_next_frame.clear()
            else:
                self._frame_cache.pop(instance_id, None)
                self._await_next_frame[instance_id] = now

    def _mark_post_action_frame_boundary(self, instance_id: str) -> None:
        not_before = time.monotonic() + _POST_ACTION_FRAME_SETTLE_S
        with self._frame_cache_lock:
            self._frame_cache.pop(instance_id, None)
            self._await_next_frame[instance_id] = max(
                not_before,
                self._await_next_frame.get(instance_id, 0.0),
            )

    def _pop_next_frame_boundary(self, instance_id: str) -> float | None:
        with self._frame_cache_lock:
            return self._await_next_frame.pop(instance_id, None)

    def _clear_settle_boundary_locked(
        self, instance_id: str, frame_ts: float
    ) -> None:
        """Drop the post-action settle boundary only if ``frame_ts`` reached it.

        Caller must hold ``_frame_cache_lock``. A frame captured *before* the
        boundary — e.g. a rolling-loop tick that fired mid-animation right after
        a tap — must leave the boundary intact, otherwise the next DSL match
        consumes that pre-tap frame from the cache and clicks the same button
        again (the double-click / popup-close loop).
        """
        boundary = self._await_next_frame.get(instance_id)
        if boundary is not None and frame_ts >= boundary:
            self._await_next_frame.pop(instance_id, None)

    def capture_screen_bgr_adb(self, instance_id: str) -> np.ndarray:
        """Direct ``adb exec-out screencap`` — rolling loop only; also publishes to ``frame_bus``."""
        img, transform, err = adb_screencap_bgr_with_transform(
            self._adb_bin(),
            self._get_serial(instance_id),
        )
        if img is None:
            raise RuntimeError(err)
        frame_bus.publish(instance_id, img, transform=transform)
        with self._frame_cache_lock:
            now = time.monotonic()
            self._frame_cache[instance_id] = (now, img, transform)
            self._clear_settle_boundary_locked(instance_id, now)
        return img

    def _get_scrcpy_client(self, instance_id: str) -> ScrcpyClient:
        """Lazy-start the shared scrcpy server for ``instance_id``.

        Raises ``RuntimeError`` (or whatever the underlying start path threw)
        when scrcpy can't be brought up — by design. There is no silent
        adb fallback: the operator configured ``screenshot_backend=scrcpy``
        / ``input_backend=scrcpy`` and quietly degrading to ADB would mask
        the real problem (device offline, JAR push denied, server crash, …)
        behind a slow-but-functional bot. Fix scrcpy, restart.
        """
        with self._scrcpy_lock:
            slot = next(
                (i for i, inst in enumerate(self._settings.instances)
                 if inst.instance_id == instance_id),
                0,
            )
            port = _SCRCPY_PORT_BASE + slot
            client = get_or_create_scrcpy_client(
                serial=self._get_serial(instance_id),
                adb_bin=self._adb_bin(),
                port=port,
            )
            if client.is_alive():
                return client
            try:
                client.start()
            except Exception as exc:
                # Drop the dead client so the next call re-creates a fresh
                # instance with current adb_bin / port — without this a
                # botched start would freeze the (serial → ScrcpyClient)
                # registry on a defunct object.
                with contextlib.suppress(Exception):
                    close_scrcpy_client(self._get_serial(instance_id))
                msg = f"scrcpy unavailable for {instance_id}: {exc}"
                raise RuntimeError(msg) from exc
            return client

    def _normalize_and_publish_frame(
        self,
        instance_id: str,
        img: np.ndarray,
    ) -> np.ndarray:
        normalized, transform = normalize_adb_frame_bgr_with_transform(
            img,
            target_size=GAME_FRAME_SIZE,
        )
        frame_bus.publish(instance_id, normalized, transform=transform)
        with self._frame_cache_lock:
            now = time.monotonic()
            self._frame_cache[instance_id] = (now, normalized, transform)
            self._clear_settle_boundary_locked(instance_id, now)
        return normalized

    def capture_screen_bgr_scrcpy(
        self,
        instance_id: str,
        *,
        not_before_s: float | None = None,
    ) -> np.ndarray:
        """Capture via scrcpy H.264 stream. Raises on failure — no adb fallback."""
        client = self._get_scrcpy_client(instance_id)
        img, capture_err = client.read_latest_frame_bgr(
            timeout_s=self._NEXT_FRAME_TIMEOUT_S,
            not_before_s=not_before_s,
        )
        if img is None:
            # Tear down the dead client so the next tick can attempt a fresh
            # start, then raise — slow-and-functional ADB fallback is exactly
            # the masking behaviour we removed by design.
            with contextlib.suppress(Exception):
                close_scrcpy_client(self._get_serial(instance_id))
            msg = (
                f"scrcpy capture failed for {instance_id}: "
                f"{capture_err or 'no frame received'}"
            )
            raise RuntimeError(msg)
        return self._normalize_and_publish_frame(instance_id, img)

    def capture_screen_bgr_direct(self, instance_id: str) -> np.ndarray:
        """Direct screenshot using the instance's configured backend.

        ``quartz`` is the default backend for speed. If WindowServer capture is
        unavailable or the window cannot be found, fall back to ADB so scenarios
        keep making progress instead of stalling on host-window state.
        """
        inst = self._get_instance(instance_id)
        backend = (inst.screenshot_backend or "").strip().lower()
        if not backend:
            # Smart default: physical Android device → scrcpy (fast video stream),
            # emulator / BlueStacks (localhost serial) → quartz (no USB hop).
            backend = (
                "quartz"
                if is_emulator_adb_serial(self._get_serial(instance_id))
                else "scrcpy"
            )
        if backend == "adb":
            return self.capture_screen_bgr_adb(instance_id)
        if backend == "scrcpy":
            return self.capture_screen_bgr_scrcpy(instance_id)
        if backend != "quartz":
            logger.warning(
                "Unknown screenshot backend %r for %s; using quartz",
                inst.screenshot_backend,
                instance_id,
            )
        try:
            img = quartz_screencap_bgr(
                instance_id=instance_id,
                quartz_window_id=inst.quartz_window_id,
                quartz_window_title=inst.quartz_window_title,
                quartz_crop=inst.quartz_crop,
            )
        except Exception:
            return self.capture_screen_bgr_adb(instance_id)
        frame_bus.publish(instance_id, img)
        with self._frame_cache_lock:
            now = time.monotonic()
            self._frame_cache[instance_id] = (now, img, None)
            self._clear_settle_boundary_locked(instance_id, now)
        return img

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        """Latest BGR frame for ``instance_id``.

        For scrcpy backend, read directly from the scrcpy reader thread's
        cache — frames stream at ~30 FPS, so the cached frame is at most
        ~33ms old. The rolling loop's 2s ``frame_bus`` cadence would otherwise
        serialise every post-tap capture behind it.

        For other backends, wait on ``frame_bus`` (fed by the rolling loop);
        fall back to a direct capture if nothing was published within the
        timeout (cold race, device offline, or capture stalled).

        When ``screenshot_backend=scrcpy`` is configured and scrcpy is
        unavailable, this raises rather than silently routing through ADB —
        a slow-but-functional bot would mask the real fault.
        """
        inst = self._get_instance(instance_id)
        backend = (inst.screenshot_backend or "").strip().lower()
        if backend == "scrcpy":
            not_before = self._pop_next_frame_boundary(instance_id)
            if not_before is not None:
                return self.capture_screen_bgr_scrcpy(
                    instance_id,
                    not_before_s=not_before,
                )
            return self._capture_screen_bgr_scrcpy_fast(instance_id)

        await_next = self._pop_next_frame_boundary(instance_id) is not None
        try:
            if await_next:
                snap = frame_bus.wait_for_next_snapshot(
                    instance_id, timeout=self._NEXT_FRAME_TIMEOUT_S
                )
            else:
                snap = frame_bus.wait_for_first_snapshot(
                    instance_id, timeout=self._FIRST_FRAME_TIMEOUT_S
                )
            img = snap.frame_bgr
            transform = snap.transform
        except frame_bus.FrameBusTimeout:
            logger.info(
                "frame_bus: timed out waiting for %r — direct screenshot "
                "(rolling loop cold, paused, or not publishing frames)",
                instance_id,
            )
            return self.capture_screen_bgr_direct(instance_id)
        with self._frame_cache_lock:
            self._frame_cache[instance_id] = (time.monotonic(), img, transform)
        return img

    def _capture_screen_bgr_scrcpy_fast(self, instance_id: str) -> np.ndarray:
        """Read directly from the scrcpy reader cache (sub-200ms typical).

        Raises ``RuntimeError`` when scrcpy is unavailable for this instance —
        ``screenshot_backend=scrcpy`` means scrcpy is the only acceptable
        source. Quietly falling through to ADB would mask the real failure.
        """
        client = self._get_scrcpy_client(instance_id)
        img, err = client.read_latest_frame_bgr(
            timeout_s=_SCRCPY_NEXT_FRAME_TIMEOUT_S,
        )
        if img is None:
            msg = (
                f"scrcpy capture failed for {instance_id}: "
                f"{err or 'no frame received within timeout'}"
            )
            raise RuntimeError(msg)
        return self._normalize_and_publish_frame(instance_id, img)

    def capture_screen_bgr_cached(
        self,
        instance_id: str,
        *,
        max_age_ms: float | None = None,
    ) -> np.ndarray:
        """Return the most recent framebuffer if no action has invalidated it.

        DSL match siblings (``while_match``→``while_match``→…) all probe the
        same screen state when nothing taps in between, so this returns the
        cached frame across them and skips the ADB screencap.  Any
        state-changing call (tap/swipe/long_tap/type_text/restart_application/
        ensure_game_foreground) drops the cache, forcing a fresh capture.

        ``max_age_ms`` adds an additional staleness gate for callers that need
        a recent frame even when no action has invalidated the cache — OCR
        reads of timers/countdowns, for instance, must not run against a
        300-ms-old frame just because nothing has tapped. ``None`` (default)
        preserves the tap-invalidation-only behavior; pass a positive number
        to require a frame no older than that many milliseconds.
        """
        now = time.monotonic()
        with self._frame_cache_lock:
            cached = self._frame_cache.get(instance_id)
            boundary = self._await_next_frame.get(instance_id)
            if cached is not None:
                ts, frame, _transform = cached
                # A pending settle boundary means a state-changing action (tap/
                # swipe/…) fired after this frame was captured. Returning it now
                # would analyze — and re-click — the pre-action screen, so treat
                # any frame captured before the boundary as unusable and fall
                # through to a fresh, settled capture.
                settled = boundary is None or ts >= boundary
                fresh_enough = (
                    max_age_ms is None or (now - ts) * 1000.0 <= max_age_ms
                )
                if settled and fresh_enough:
                    return frame
                # Force the fall-through capture to wait for a genuinely new
                # frame. Don't clobber an existing (future) boundary with an
                # earlier timestamp — that would shorten the settle window.
                if boundary is None:
                    self._await_next_frame[instance_id] = now
        inst = self._get_instance(instance_id)
        backend = (inst.screenshot_backend or "").strip().lower()
        if backend == "scrcpy":
            not_before = self._pop_next_frame_boundary(instance_id)
            return self.capture_screen_bgr_scrcpy(
                instance_id,
                not_before_s=not_before,
            )
        return self.capture_screen_bgr(instance_id)

    def tap(
        self,
        instance_id: str,
        point: Point,
        *,
        approval_region: str | None = None,
        approval_source: str | None = None,
        approval_context: dict[str, object] | None = None,
        revalidate: Callable[[], bool] | None = None,
        hold_ms: int = 0,
    ) -> bool:
        adb_point = self._to_adb_point(instance_id, point)
        self.invalidate_frame_cache(instance_id)
        ok = self._controller(instance_id).tap(
            adb_point,
            preview_point=point,
            approval_region=approval_region,
            approval_source=approval_source,
            approval_context=approval_context,
            revalidate=revalidate,
            hold_ms=hold_ms,
        )
        if ok:
            self._mark_post_action_frame_boundary(instance_id)
        return ok

    def attach_approval_preview(self, instance_id: str, payload: dict[str, object]) -> None:
        self._controller(instance_id).attach_approval_preview(payload)

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
        adb_start = self._to_adb_point(instance_id, start)
        adb_end = self._to_adb_point(instance_id, end)
        self.invalidate_frame_cache(instance_id)
        ok = self._controller(instance_id).swipe(
            adb_start,
            adb_end,
            timedelta(milliseconds=duration_ms),
            preview_start=start,
            preview_end=end,
        )
        if ok:
            self._mark_post_action_frame_boundary(instance_id)
        return ok

    def swipe_direction(
        self, instance_id: str, direction: str, delta: int, duration_ms: int = 300
    ) -> bool:
        frame_w, frame_h = GAME_FRAME_SIZE
        start, end = _directional_swipe_points(frame_w, frame_h, direction, delta)
        return self.swipe(instance_id, start, end, duration_ms=duration_ms)

    def long_tap(self, instance_id: str, point: Point, duration_ms: int = 800) -> bool:
        adb_point = self._to_adb_point(instance_id, point)
        self.invalidate_frame_cache(instance_id)
        ok = self._controller(instance_id).swipe(
            adb_point,
            adb_point,
            timedelta(milliseconds=duration_ms),
            preview_start=point,
            preview_end=point,
        )
        if ok:
            self._mark_post_action_frame_boundary(instance_id)
        return ok

    def system_back(self, instance_id: str) -> bool:
        self.invalidate_frame_cache(instance_id)
        ok = self._controller(instance_id).system_back()
        if ok:
            self._mark_post_action_frame_boundary(instance_id)
        return ok

    def back(self, instance_id: str) -> None:
        logger.debug("BotActions.back(%s): no-op (phone BACK not allowed)", instance_id)

    def home(self, instance_id: str) -> None:
        logger.debug("BotActions.home(%s): no-op (phone HOME not allowed)", instance_id)

    def type_text(self, instance_id: str, text: str) -> bool:
        self.invalidate_frame_cache(instance_id)
        ok = self._controller(instance_id).type_text(text)
        if ok:
            self._mark_post_action_frame_boundary(instance_id)
        return ok

    def restart_application(self, instance_id: str) -> bool:
        self.invalidate_frame_cache(instance_id)
        ok = self._controller(instance_id).restart_application(self._get_game(instance_id))
        if ok:
            self._mark_post_action_frame_boundary(instance_id)
        return ok

    def ensure_game_foreground(
        self,
        instance_id: str,
        *,
        require_approval: bool = True,
    ) -> bool:
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).ensure_game_foreground(
            self._get_game(instance_id),
            require_approval=require_approval,
        )

    def is_game_foreground(self, instance_id: str) -> bool:
        """True if the configured game on ``instance_id`` is the resumed top activity."""
        return self._controller(instance_id).is_game_foreground(self._get_game(instance_id))

    def current_foreground_activity(self, instance_id: str) -> str:
        """Resumed foreground component (``pkg/activity``) on ``instance_id`` (or "")."""
        return self._controller(instance_id).current_foreground_activity()

    def is_game_running(self, instance_id: str) -> bool:
        """True if the configured game's process is alive on ``instance_id``.

        Aliveness, not foreground — the reliable health signal on BlueStacks
        where the resumed-activity parse false-negatives. See
        ``AdbController.is_game_running``.
        """
        return self._controller(instance_id).is_game_running(self._get_game(instance_id))

    def detect_game_process(self, instance_id: str) -> ProcessDetection:
        """Structured game-process probe for ``instance_id`` (found/pids/method/error).

        Use over :meth:`is_game_running` when the caller needs the PIDs or wants
        to distinguish "process dead" from "detection failed" (``error`` set).
        """
        return self._controller(instance_id).detect_game_process(self._get_game(instance_id))

    def list_installed_games(self, instance_id: str) -> list[str]:
        """Game ids whose packages are installed on the device behind ``instance_id``."""
        return self._controller(instance_id).list_installed_games()
