"""ADB-based input controller for BlueStacks instances.

All touch input goes through `adb shell input` — never native macOS mouse events.
Screen capture stays on Quartz (BlueStacks renders to a macOS window).
"""

from __future__ import annotations

import logging
import random
import subprocess
from datetime import timedelta

from config.loader import get_settings
from layout.types import Point, Region

logger = logging.getLogger(__name__)

_GAME_PACKAGE = "com.gof.global"


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def _jitter(value: int, spread: int) -> int:
    """Apply ±spread pixel random jitter."""
    if spread <= 0:
        return value
    return value + random.randint(-spread, spread)


class AdbController:
    """ADB wrapper matching the Go DeviceController interface."""

    def __init__(self, device_serial: str) -> None:
        self._serial = device_serial
        self._verify_available()
        self.set_brightness(70)
        self.set_heads_up_notifications(enabled=False)

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    @staticmethod
    def list_devices() -> list[str]:
        out = _adb("devices")
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
        out = _adb("devices")
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
        import time
        time.sleep(2)
        self._shell("monkey", "-p", _GAME_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1")
        logger.info("Application restarted on %s", self._serial)

    # ------------------------------------------------------------------
    # Touch input — all with jitter to simulate natural fingers
    # ------------------------------------------------------------------

    def tap(self, point: Point) -> None:
        """Tap center of Point with ±2 px jitter."""
        x = _jitter(point.x, 2)
        y = _jitter(point.y, 2)
        self._shell("input", "tap", str(x), str(y))
        logger.debug("Tap (%d, %d) on %s", x, y, self._serial)

    def tap_region(self, region: Region) -> None:
        """Tap center of Region with ±5% size jitter, clamped inside bounds."""
        cx = region.x + region.w // 2
        cy = region.y + region.h // 2
        spread_x = max(1, int(region.w * 0.05))
        spread_y = max(1, int(region.h * 0.05))
        x = _clamp(_jitter(cx, spread_x), region.x, region.x + region.w - 1)
        y = _clamp(_jitter(cy, spread_y), region.y, region.y + region.h - 1)
        self._shell("input", "tap", str(x), str(y))
        logger.debug("TapRegion (%d, %d) on %s", x, y, self._serial)

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
        self._shell("input", "touchscreen", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms))
        logger.debug("Swipe (%d,%d)→(%d,%d) %dms on %s", x1, y1, x2, y2, ms, self._serial)

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
        self._shell("input", "text", escaped)
        logger.debug("Type '%s' on %s", text, self._serial)

    def key_event(self, keycode: int) -> None:
        self._shell("input", "keyevent", str(keycode))

    def back(self) -> None:
        self.key_event(4)

    def home(self) -> None:
        self.key_event(3)

    # ------------------------------------------------------------------
    # Screenshot via ADB (alternative to Quartz for headless use)
    # ------------------------------------------------------------------

    def screenshot_bytes(self) -> bytes:
        result = subprocess.run(
            ["adb", "-s", self._serial, "exec-out", "screencap", "-p"],
            capture_output=True,
            check=True,
        )
        return result.stdout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _shell(self, *args: str) -> str:
        result = subprocess.run(
            ["adb", "-s", self._serial, "shell"] + list(args),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            logger.warning(
                "ADB shell %s failed (rc=%d): %s", args, result.returncode, result.stderr.strip()
            )
        return result.stdout.strip()


def _adb(*args: str) -> str:
    result = subprocess.run(["adb"] + list(args), capture_output=True, text=True)
    return result.stdout


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
            self._controllers[instance_id] = AdbController(serial)
        return self._controllers[instance_id]

    def _get_serial(self, instance_id: str) -> str:
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                return inst.bluestacks_window_title  # ADB serial for BlueStacks
        raise ValueError(f"Unknown instance_id: {instance_id!r}")

    def window_substring_for_capture(self, instance_id: str) -> str:
        """Substring for Quartz CGWindowList (owner/name); may differ from the ADB serial."""
        for inst in self._settings.instances:
            if inst.instance_id == instance_id:
                if inst.capture_window_title:
                    return inst.capture_window_title
                return inst.bluestacks_window_title
        raise ValueError(f"Unknown instance_id: {instance_id!r}")

    def _get_window_title(self, instance_id: str) -> str:
        return self.window_substring_for_capture(instance_id)

    def tap(self, instance_id: str, point: Point) -> None:
        self._controller(instance_id).tap(point)

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
        self._controller(instance_id).back()

    def home(self, instance_id: str) -> None:
        self._controller(instance_id).home()

    def type_text(self, instance_id: str, text: str) -> None:
        self._controller(instance_id).type_text(text)

    def restart_application(self, instance_id: str) -> None:
        self._controller(instance_id).restart_application()
