"""Instance-aware facade: instance_id → ADB serial → AdbController."""
from __future__ import annotations

import contextlib
import logging
import threading
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from adb.controller import AdbController
from adb.frame_normalize import (
    GAME_FRAME_SIZE,
    frame_normalize_transform_for_size,
    normalized_point_to_source_point,
)
from adb.minicap import DEFAULT_PORT_BASE as _MINICAP_PORT_BASE
from adb.minicap import MinicapClient
from adb.quartz_screencap import quartz_screencap_bgr
from adb.scrcpy import DEFAULT_PORT_BASE as _SCRCPY_PORT_BASE
from adb.scrcpy import ScrcpyClient, close_scrcpy_client, get_or_create_scrcpy_client
from adb.screencap import DEFAULT_ADB_BIN, adb_screencap_bgr_with_transform
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
_DISPLAY_SETTLE_AFTER_WM_S = 5.0


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
        self._await_next_frame: set[str] = set()
        self._FIRST_FRAME_TIMEOUT_S = _FIRST_FRAME_TIMEOUT_S
        self._NEXT_FRAME_TIMEOUT_S = _NEXT_FRAME_TIMEOUT_S
        # Minicap (DeviceFarmer) clients: one persistent socket per instance.
        # ``_minicap_fallback`` tracks instances where minicap startup failed
        # so we don't retry every tick — they fall through to adb screencap.
        self._minicap_clients: dict[str, MinicapClient] = {}
        self._minicap_fallback: set[str] = set()
        self._minicap_lock = threading.Lock()
        # scrcpy (Genymobile) clients: one server process per instance, shared
        # between screenshot (here) and input (AdbController). Registry is
        # module-level — AdbController fetches the same client by serial.
        # ``_scrcpy_fallback`` matches the minicap-fallback contract: a failed
        # start means we use adb screencap for the rest of the session.
        self._scrcpy_fallback: set[str] = set()
        self._scrcpy_lock = threading.Lock()

    def _controller(self, instance_id: str) -> AdbController:
        if instance_id not in self._controllers:
            inst = self._get_instance(instance_id)
            serial = inst.bluestacks_window_title
            # Stable port per instance to avoid forward collisions across devices.
            slot = next(
                (i for i, x in enumerate(self._settings.instances)
                 if x.instance_id == instance_id),
                len(self._controllers),
            )
            from adb.minitouch import DEFAULT_PORT_BASE as _MT_PORT

            self._controllers[instance_id] = AdbController(
                instance_id,
                serial,
                adb_bin=self._adb_bin(),
                input_backend=inst.input_backend,
                minitouch_port=_MT_PORT + slot,
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
        is_fg = ctrl.is_game_foreground()
        if is_fg and not display_changed:
            logger.info(
                "Whiteout already running with matching display on %s — no restart",
                instance_id,
            )
            return
        if is_fg:
            logger.info(
                "Restarting Whiteout after display profile change on %s",
                instance_id,
            )
            ctrl.restart_application()
        else:
            logger.info(
                "Launching Whiteout on %s",
                instance_id,
            )
            ctrl.ensure_game_foreground()

    def _adb_bin(self) -> str:
        pref = (self._settings.worker.adb_executable or "").strip()
        return pref or DEFAULT_ADB_BIN

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
        with self._frame_cache_lock:
            if instance_id is None:
                self._frame_cache.clear()
                self._await_next_frame.clear()
            else:
                self._frame_cache.pop(instance_id, None)
                self._await_next_frame.add(instance_id)

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
            self._frame_cache[instance_id] = (time.monotonic(), img, transform)
            self._await_next_frame.discard(instance_id)
        return img

    def _get_minicap_client(self, instance_id: str) -> MinicapClient:
        """Lazy-start a persistent minicap client; one per instance, unique TCP port."""
        with self._minicap_lock:
            client = self._minicap_clients.get(instance_id)
            if client is not None and client.is_alive():
                return client
            # Assign a stable port: base + slot index in settings.instances.
            slot = next(
                (i for i, inst in enumerate(self._settings.instances)
                 if inst.instance_id == instance_id),
                len(self._minicap_clients),
            )
            port = _MINICAP_PORT_BASE + slot
            client = MinicapClient(
                serial=self._get_serial(instance_id),
                adb_bin=self._adb_bin(),
                port=port,
                target_size=GAME_FRAME_SIZE,
            )
            client.start()
            self._minicap_clients[instance_id] = client
            return client

    def _get_scrcpy_client(self, instance_id: str) -> ScrcpyClient | None:
        """Lazy-start the shared scrcpy server for ``instance_id``; ``None`` on fallback.

        Returns ``None`` (and pins the instance in ``_scrcpy_fallback``) when
        startup raises — both the screenshot path and the input path observe
        that signal and switch to their respective adb fallbacks for the rest
        of the session, avoiding repeated start attempts every tick.
        """
        with self._scrcpy_lock:
            if instance_id in self._scrcpy_fallback:
                return None
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
                logger.warning(
                    "scrcpy failed for %s (%s) — falling back to adb for this session",
                    instance_id, exc,
                )
                self._scrcpy_fallback.add(instance_id)
                with contextlib.suppress(Exception):
                    close_scrcpy_client(self._get_serial(instance_id))
                return None
            return client

    def capture_screen_bgr_scrcpy(self, instance_id: str) -> np.ndarray:
        """Capture via scrcpy H.264 stream. Falls back to ``capture_screen_bgr_adb`` on error."""
        client = self._get_scrcpy_client(instance_id)
        if client is None:
            return self.capture_screen_bgr_adb(instance_id)
        img: np.ndarray | None = None
        capture_err: str | None = None
        try:
            img, capture_err = client.read_latest_frame_bgr(
                timeout_s=self._NEXT_FRAME_TIMEOUT_S,
            )
        except Exception as exc:
            capture_err = str(exc)
        if img is None:
            logger.warning(
                "scrcpy failed for %s (%s) — falling back to adb screencap for this session",
                instance_id, capture_err or "no frame",
            )
            with self._scrcpy_lock:
                self._scrcpy_fallback.add(instance_id)
            with contextlib.suppress(Exception):
                close_scrcpy_client(self._get_serial(instance_id))
            return self.capture_screen_bgr_adb(instance_id)
        # scrcpy emits frames at the device's physical resolution (we pass
        # max_size=0 so no host-side resize is applied); compute a normalising
        # transform exactly like the minicap path does.
        transform = frame_normalize_transform_for_size(
            (img.shape[1], img.shape[0]),
            target_size=GAME_FRAME_SIZE,
        )
        frame_bus.publish(instance_id, img, transform=transform)
        with self._frame_cache_lock:
            self._frame_cache[instance_id] = (time.monotonic(), img, transform)
            self._await_next_frame.discard(instance_id)
        return img

    def capture_screen_bgr_minicap(self, instance_id: str) -> np.ndarray:
        """Capture via minicap JPEG stream. Falls back to ``capture_screen_bgr_adb`` on error."""
        if instance_id in self._minicap_fallback:
            return self.capture_screen_bgr_adb(instance_id)
        img: np.ndarray | None = None
        capture_err: str | None = None
        try:
            client = self._get_minicap_client(instance_id)
            img, capture_err = client.capture(timeout_s=self._NEXT_FRAME_TIMEOUT_S)
        except Exception as exc:
            capture_err = str(exc)
        if img is None:
            logger.warning(
                "minicap failed for %s (%s) — falling back to adb screencap for this session",
                instance_id, capture_err or "no frame",
            )
            with self._minicap_lock:
                self._minicap_fallback.add(instance_id)
                stale = self._minicap_clients.pop(instance_id, None)
            if stale is not None:
                with contextlib.suppress(Exception):
                    stale.close()
            return self.capture_screen_bgr_adb(instance_id)
        # Minicap delivers frames already at GAME_FRAME_SIZE (virtual P arg),
        # so the transform is a 1:1 identity from the device's physical size.
        transform = frame_normalize_transform_for_size(
            (img.shape[1], img.shape[0]),
            target_size=GAME_FRAME_SIZE,
        )
        frame_bus.publish(instance_id, img, transform=transform)
        with self._frame_cache_lock:
            self._frame_cache[instance_id] = (time.monotonic(), img, transform)
            self._await_next_frame.discard(instance_id)
        return img

    def capture_screen_bgr_direct(self, instance_id: str) -> np.ndarray:
        """Direct screenshot using the instance's configured backend.

        ``quartz`` is the default backend for speed. If WindowServer capture is
        unavailable or the window cannot be found, fall back to ADB so scenarios
        keep making progress instead of stalling on host-window state.
        """
        inst = self._get_instance(instance_id)
        backend = (inst.screenshot_backend or "").strip().lower()
        if not backend:
            # Smart default: physical Android device → minicap (fast push stream),
            # emulator / BlueStacks (localhost serial) → quartz (no USB hop).
            backend = (
                "quartz"
                if is_emulator_adb_serial(self._get_serial(instance_id))
                else "minicap"
            )
        if backend == "adb":
            return self.capture_screen_bgr_adb(instance_id)
        if backend == "minicap":
            return self.capture_screen_bgr_minicap(instance_id)
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
            self._frame_cache[instance_id] = (time.monotonic(), img, None)
            self._await_next_frame.discard(instance_id)
        return img

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        """Framebuffer BGR from ``frame_bus``, normally fed by the rolling ADB loop.

        If nothing was published within the timeout (cold race, rolling paused for
        device-offline, or ADB not returning screenshots), fall back to a direct
        configured direct backend so matchers / overlay DSL can still run.
        """
        with self._frame_cache_lock:
            await_next = instance_id in self._await_next_frame
            if await_next:
                self._await_next_frame.discard(instance_id)
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
            logger.warning(
                "frame_bus: timed out waiting for %r — direct screenshot "
                "(rolling loop cold, paused, or not publishing frames)",
                instance_id,
            )
            return self.capture_screen_bgr_direct(instance_id)
        with self._frame_cache_lock:
            self._frame_cache[instance_id] = (time.monotonic(), img, transform)
        return img

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
        with self._frame_cache_lock:
            cached = self._frame_cache.get(instance_id)
            if cached is not None:
                ts, frame, _transform = cached
                if max_age_ms is None or (time.monotonic() - ts) * 1000.0 <= max_age_ms:
                    return frame
                self._await_next_frame.add(instance_id)
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
        return self._controller(instance_id).tap(
            adb_point,
            preview_point=point,
            approval_region=approval_region,
            approval_source=approval_source,
            approval_context=approval_context,
            revalidate=revalidate,
            hold_ms=hold_ms,
        )

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
        return self._controller(instance_id).swipe(
            adb_start,
            adb_end,
            timedelta(milliseconds=duration_ms),
            preview_start=start,
            preview_end=end,
        )

    def swipe_direction(
        self, instance_id: str, direction: str, delta: int, duration_ms: int = 300
    ) -> bool:
        frame_w, frame_h = GAME_FRAME_SIZE
        cx, cy = frame_w // 2, frame_h // 2
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
                msg = f"Unknown swipe direction: {direction!r}"
                raise ValueError(msg)
        return self.swipe(instance_id, start, end, duration_ms=duration_ms)

    def long_tap(self, instance_id: str, point: Point, duration_ms: int = 800) -> bool:
        adb_point = self._to_adb_point(instance_id, point)
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).swipe(
            adb_point,
            adb_point,
            timedelta(milliseconds=duration_ms),
            preview_start=point,
            preview_end=point,
        )

    def system_back(self, instance_id: str) -> bool:
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).system_back()

    def back(self, instance_id: str) -> None:
        logger.debug("BotActions.back(%s): no-op (phone BACK not allowed)", instance_id)

    def home(self, instance_id: str) -> None:
        logger.debug("BotActions.home(%s): no-op (phone HOME not allowed)", instance_id)

    def type_text(self, instance_id: str, text: str) -> bool:
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).type_text(text)

    def restart_application(self, instance_id: str) -> None:
        self.invalidate_frame_cache(instance_id)
        self._controller(instance_id).restart_application()

    def ensure_game_foreground(self, instance_id: str) -> None:
        self.invalidate_frame_cache(instance_id)
        self._controller(instance_id).ensure_game_foreground()

    def is_game_foreground(self, instance_id: str) -> bool:
        """True if ``adb dumpsys activity`` reports Whiteout as resumed top activity."""
        return self._controller(instance_id).is_game_foreground()
