"""Standalone capture/tap wrapper over the project's ADB interfaces.

``radar`` runs as a one-shot CLI outside the worker: no Redis, no scrcpy
server, no approval UI. This wrapper builds on the same ``AdbController`` /
``adb_screencap_bgr`` primitives the bot uses but keeps the scan loop free of
worker-side machinery.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adb.controller import AdbController
from adb.screencap import MSG_ADB_NOT_FOUND, adb_screencap_bgr, resolve_adb_executable

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np


class ScanStopped(RuntimeError):
    """Operator pressed Stop — abandon the current step immediately."""


class RadarDevice:
    """Capture + tap for one device, usable without the worker stack.

    ``abort_check`` (when set) is consulted before every device operation —
    every blocking scan loop (stabilization, label guard, chunked swipes, zoom
    retries) funnels through capture/tap/swipe, so a stop request interrupts
    within one capture interval instead of waiting out the whole scan step.
    """

    def __init__(
        self,
        serial: str,
        adb_bin: str = "adb",
        abort_check: Callable[[], bool] | None = None,
    ) -> None:
        resolved = resolve_adb_executable(adb_bin)
        if resolved is None:
            raise RuntimeError(MSG_ADB_NOT_FOUND)
        self._adb_bin = resolved
        self._serial = serial
        self.abort_check = abort_check
        # input_backend="adb": scrcpy needs the worker-owned client; the
        # constructor also verifies the device is attached.
        self._controller = AdbController(
            "radar",
            serial,
            adb_bin=resolved,
            input_backend="adb",
        )

    @property
    def serial(self) -> str:
        return self._serial

    def _maybe_abort(self) -> None:
        if self.abort_check is not None and self.abort_check():
            msg = "stop requested by the operator"
            raise ScanStopped(msg)

    def tap(self, x: float, y: float) -> None:
        self._maybe_abort()
        # Raw emit on purpose: the public tap() adds ±1-3 px humanizing jitter
        # and talks to Redis for approvals/previews. The radar grid is a
        # precomputed constant — taps must be deterministic and offline.
        self._controller._emit_tap(int(round(x)), int(round(y)))

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None:
        self._maybe_abort()
        # No-fling drag (hold before lift-off): `input swipe` releases at full
        # speed and the map flings an unpredictable extra distance, which
        # breaks the navigation prior and frame overlap. Raw emit path on
        # purpose: no approval UI, no endpoint jitter.
        args = (
            int(round(x1)),
            int(round(y1)),
            int(round(x2)),
            int(round(y2)),
            duration_ms,
        )
        if not self._controller._emit_drag_no_fling(*args):
            # motionevent unsupported on this device — plain swipe still works,
            # the stitcher's prior tolerance absorbs the fling drift.
            self._controller._emit_swipe_straight(*args)

    def capture(self) -> np.ndarray:
        """Screenshot as a normalized 720×1280 BGR array."""
        self._maybe_abort()
        img, err = adb_screencap_bgr(self._adb_bin, self._serial)
        if img is None:
            msg = f"screencap failed on {self._serial}: {err}"
            raise RuntimeError(msg)
        return img


def pick_serial(adb_bin: str = "adb") -> str:
    """The single attached device, or a clear error telling the user to choose."""
    serials = AdbController.list_devices(adb_bin)
    if len(serials) == 1:
        return serials[0]
    if not serials:
        msg = "no ADB devices connected — start the emulator and check `adb devices`"
        raise RuntimeError(msg)
    msg = f"multiple ADB devices connected ({', '.join(serials)}) — pass --serial"
    raise RuntimeError(msg)
