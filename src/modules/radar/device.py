"""Standalone capture/tap wrapper over the project's ADB interfaces.

``radar`` runs as a one-shot CLI outside the worker: no Redis, no scrcpy
server, no approval UI. This wrapper builds on the same ``AdbController`` /
``adb_screencap_bgr`` primitives the bot uses but keeps the scan loop free of
worker-side machinery.
"""

from __future__ import annotations

import re
import subprocess
import time
from typing import TYPE_CHECKING

from adb.controller import AdbController
from adb.screencap import MSG_ADB_NOT_FOUND, adb_screencap_bgr, resolve_adb_executable

if TYPE_CHECKING:
    from collections.abc import Callable

    import numpy as np

# Linux input event codes used for the multitouch pinch (sendevent is numeric).
_EV_SYN, _SYN_REPORT, _SYN_MT_REPORT = 0, 0, 2
_EV_KEY, _BTN_TOUCH = 1, 330
_EV_ABS, _ABS_MT_TRACKING_ID, _ABS_MT_POSITION_X, _ABS_MT_POSITION_Y = 3, 57, 53, 54
_SCREEN_W, _SCREEN_H = 720, 1280


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

    def _adb_shell(self, cmd: str) -> str:
        return subprocess.run(
            [self._adb_bin, "-s", self._serial, "shell", cmd],
            capture_output=True,
            text=True,
            check=False,
        ).stdout

    def _touch_device(self) -> tuple[str, int, int] | None:
        """``(event_path, max_x, max_y)`` of the multitouch (``ABS_MT_POSITION``)
        input device, parsed from ``getevent -pl``; ``None`` if none is found.

        Cached after the first probe — the touchscreen does not change.
        """
        cached = getattr(self, "_touch_cache", False)
        if cached is not False:
            return cached  # type: ignore[return-value]
        dev: str | None = None
        mx: int | None = None
        my: int | None = None
        result: tuple[str, int, int] | None = None
        for line in self._adb_shell("getevent -pl").splitlines():
            head = re.search(r"add device \d+:\s*(\S+)", line)
            if head:
                dev, mx, my = head.group(1), None, None
                continue
            if dev is None:
                continue
            xm = re.search(r"ABS_MT_POSITION_X.*max\s+(\d+)", line)
            if xm:
                mx = int(xm.group(1))
            ym = re.search(r"ABS_MT_POSITION_Y.*max\s+(\d+)", line)
            if ym:
                my = int(ym.group(1))
            if mx and my:
                result = (dev, mx, my)
                break
        self._touch_cache = result
        return result

    def zoom_out(self, steps: int = 7, *, settle_s: float = 0.25) -> bool:
        """Pinch the city map fully out via multitouch ``sendevent``.

        The radar scan wants a FIXED, fully-zoomed-out scale for repeatable
        distances (and so localization at scan time matches navigation time).
        ``adb input`` is single-touch only, so this drives the touchscreen
        directly: two contacts converge toward the screen centre, frame by
        frame with a short device-side delay so the game registers a real pinch
        gesture (a single instantaneous burst is ignored). The game clamps at
        minimum zoom, so ``steps`` just needs to be "enough" to bottom out —
        the landing scale is then identical every call.

        Type-A multitouch (BlueStacks "Virtual Touch" and similar). Returns
        False when no ``ABS_MT_POSITION`` device is present (nothing tapped).
        """
        td = self._touch_device()
        if td is None:
            return False
        dev, maxx, maxy = td

        def ex(x: float) -> int:
            return max(0, min(maxx, round(x / _SCREEN_W * maxx)))

        def ey(y: float) -> int:
            return max(0, min(maxy, round(y / _SCREEN_H * maxy)))

        def se(t: int, c: int, v: int) -> str:
            return f"sendevent {dev} {t} {c} {v}"

        cx, cy = _SCREEN_W / 2.0, _SCREEN_H / 2.0
        # Two fingers start spread diagonally and converge → zoom out.
        a0, a1 = (cx - 160, cy - 160), (cx - 30, cy - 30)
        b0, b1 = (cx + 160, cy + 160), (cx + 30, cy + 30)
        frames = 16

        def _frame(ax: float, ay: float, bx: float, by: float) -> list[str]:
            return [
                se(_EV_ABS, _ABS_MT_TRACKING_ID, 0),
                se(_EV_ABS, _ABS_MT_POSITION_X, ex(ax)),
                se(_EV_ABS, _ABS_MT_POSITION_Y, ey(ay)),
                se(_EV_SYN, _SYN_MT_REPORT, 0),
                se(_EV_ABS, _ABS_MT_TRACKING_ID, 1),
                se(_EV_ABS, _ABS_MT_POSITION_X, ex(bx)),
                se(_EV_ABS, _ABS_MT_POSITION_Y, ey(by)),
                se(_EV_SYN, _SYN_MT_REPORT, 0),
                se(_EV_SYN, _SYN_REPORT, 0),
                "sleep 0.018",
            ]

        for _ in range(max(1, steps)):
            self._maybe_abort()
            parts = [se(_EV_KEY, _BTN_TOUCH, 1)]
            for i in range(frames + 1):
                t = i / frames
                parts += _frame(
                    a0[0] + (a1[0] - a0[0]) * t,
                    a0[1] + (a1[1] - a0[1]) * t,
                    b0[0] + (b1[0] - b0[0]) * t,
                    b0[1] + (b1[1] - b0[1]) * t,
                )
            parts += [  # lift both contacts
                se(_EV_SYN, _SYN_MT_REPORT, 0),
                se(_EV_KEY, _BTN_TOUCH, 0),
                se(_EV_SYN, _SYN_REPORT, 0),
            ]
            self._adb_shell(" ; ".join(parts))
            time.sleep(settle_s)
        return True


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
