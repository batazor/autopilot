"""Instance-aware facade: instance_id → ADB serial → AdbController."""
from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING

from adb.controller import AdbController
from adb.screencap import DEFAULT_ADB_BIN, adb_screencap_bgr
from worker import frame_bus

if TYPE_CHECKING:
    import numpy as np

    from config.loader import Settings
    from layout.types import Point

logger = logging.getLogger(__name__)

_FIRST_FRAME_TIMEOUT_S = 30.0
_NEXT_FRAME_TIMEOUT_S = 3.0


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
        # Each entry is ``(monotonic_ts, frame)``; callers that pass
        # ``max_age_ms`` to ``capture_screen_bgr_cached`` use the timestamp to
        # opt out of stale frames (OCR, timer reads). ``None`` keeps the
        # tap-invalidation-only behavior used by ``match`` / ``while_match``.
        self._frame_cache: dict[str, tuple[float, np.ndarray]] = {}
        self._await_next_frame: set[str] = set()
        self._FIRST_FRAME_TIMEOUT_S = _FIRST_FRAME_TIMEOUT_S
        self._NEXT_FRAME_TIMEOUT_S = _NEXT_FRAME_TIMEOUT_S

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
        msg = f"Unknown instance_id: {instance_id!r}"
        raise ValueError(msg)

    def _adb_bin(self) -> str:
        pref = (self._settings.worker.adb_executable or "").strip()
        return pref or DEFAULT_ADB_BIN

    def invalidate_frame_cache(self, instance_id: str | None = None) -> None:
        """Drop the cached framebuffer for ``instance_id`` (or all instances)."""
        if instance_id is None:
            self._frame_cache.clear()
            self._await_next_frame.clear()
        else:
            self._frame_cache.pop(instance_id, None)
            self._await_next_frame.add(instance_id)

    def capture_screen_bgr_adb(self, instance_id: str) -> np.ndarray:
        """Direct ``adb exec-out screencap`` — rolling loop only; also publishes to ``frame_bus``."""
        img, err = adb_screencap_bgr(self._adb_bin(), self._get_serial(instance_id))
        if img is None:
            raise RuntimeError(err)
        frame_bus.publish(instance_id, img)
        self._frame_cache[instance_id] = (time.monotonic(), img)
        self._await_next_frame.discard(instance_id)
        return img

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        """Framebuffer BGR from ``frame_bus``, normally fed by the rolling ADB loop.

        If nothing was published within the timeout (cold race, rolling paused for
        device-offline, or ADB not returning screenshots), fall back to a direct
        ``adb screencap`` so matchers / overlay DSL can still run.
        """
        try:
            if instance_id in self._await_next_frame:
                self._await_next_frame.discard(instance_id)
                img = frame_bus.wait_for_next(
                    instance_id, timeout=self._NEXT_FRAME_TIMEOUT_S
                )
            else:
                img = frame_bus.wait_for_first(
                    instance_id, timeout=self._FIRST_FRAME_TIMEOUT_S
                )
        except frame_bus.FrameBusTimeout:
            logger.warning(
                "frame_bus: timed out waiting for %r — direct ADB screencap "
                "(rolling loop cold, paused, or ADB not publishing frames)",
                instance_id,
            )
            img = self.capture_screen_bgr_adb(instance_id)
        self._frame_cache[instance_id] = (time.monotonic(), img)
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
        cached = self._frame_cache.get(instance_id)
        if cached is not None:
            ts, frame = cached
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
    ) -> bool:
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).tap(
            point,
            approval_region=approval_region,
            approval_source=approval_source,
            approval_context=approval_context,
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
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).swipe(start, end, timedelta(milliseconds=duration_ms))

    def swipe_direction(
        self, instance_id: str, direction: str, delta: int, duration_ms: int = 300
    ) -> bool:
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).swipe_direction(
            direction, delta, timedelta(milliseconds=duration_ms)
        )

    def long_tap(self, instance_id: str, point: Point, duration_ms: int = 800) -> bool:
        self.invalidate_frame_cache(instance_id)
        return self._controller(instance_id).long_tap(point, timedelta(milliseconds=duration_ms))

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
