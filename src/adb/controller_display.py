"""Display / resolution / system-settings methods for :class:`adb.controller.AdbController`."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from adb.controller_types import _clamp

if TYPE_CHECKING:
    from config.device_display import DeviceDisplayConfig

if TYPE_CHECKING:
    from adb._controller_host import _ControllerHost as _Base
else:
    _Base = object

logger = logging.getLogger(__name__)


class AdbDisplayMixin(_Base):
    """wm size/density overrides, brightness, and screen-resolution probing."""

    def apply_display_config(
        self,
        config: DeviceDisplayConfig,
        *,
        serial: str | None = None,
    ) -> bool:
        """Apply wm size/density, brightness, and screen-on settings via ADB.

        Returns True iff the ``wm size`` / ``wm density`` overrides were actually
        changed on this call — callers (notably :meth:`BotActions.apply_display_then_launch_game`)
        use this to decide whether the game needs a restart to pick up a new
        display profile. Brightness / heads-up / keep-screen-on are not counted
        because the game does not need a restart to observe them.
        """
        import re

        from adb.serial import is_emulator_adb_serial

        target_serial = (serial or self._serial).strip()
        apply_wm = (
            config.wm_size_on_emulator is True
            or not is_emulator_adb_serial(target_serial)
        )
        size_re = re.compile(r"^\d+x\d+$")
        wm_changed = False

        if apply_wm and config.size:
            size = config.size.strip()
            if size.lower() == "auto":
                phys = self._read_physical_wm_size()
                if phys is not None:
                    from adb.frame_normalize import wm_size_for_physical

                    size = wm_size_for_physical(phys[0], phys[1])
                else:
                    size = ""
            if size and size_re.match(size):
                current = self._read_effective_wm_size()
                if current != size:
                    self._shell("wm", "size", size)
                    self._screen_resolution = None
                    wm_changed = True
                    logger.info("Display: wm size %s on %s", size, self._serial)
                else:
                    logger.debug(
                        "Display: wm size already %s on %s — skipping",
                        size,
                        self._serial,
                    )
            elif size:
                logger.warning("Display: invalid size %r — skipped", config.size)

        if apply_wm and config.density is not None:
            target_density = int(config.density)
            current_density = self._read_effective_wm_density()
            if current_density != target_density:
                self._shell("wm", "density", str(target_density))
                wm_changed = True
                logger.info("Display: wm density %s on %s", target_density, self._serial)
            else:
                logger.debug(
                    "Display: wm density already %s on %s — skipping",
                    target_density,
                    self._serial,
                )

        # Manual brightness mode so ``brightness_percent`` via ADB is not overridden by auto.
        self._shell("settings", "put", "system", "screen_brightness_mode", "0")

        if config.brightness_percent is not None:
            self.set_brightness(int(config.brightness_percent))

        # Heads-up banners over the game UI; restored by :meth:`reset_display_overrides`.
        self.set_heads_up_notifications(enabled=False)

        if config.keep_screen_on:
            if config.screen_off_timeout_ms is not None:
                self._shell(
                    "settings",
                    "put",
                    "system",
                    "screen_off_timeout",
                    str(int(config.screen_off_timeout_ms)),
                )
            # 3 = stay awake on AC, USB, and wireless.
            self._shell("settings", "put", "global", "stay_on_while_plugged_in", "3")
            self._shell("svc", "power", "stayon", "true")

        return wm_changed

    def _read_effective_wm_size(self) -> str:
        """``WxH`` of the active wm override, or empty if none / unparseable.

        ``wm size`` prints ``Physical size: WxH`` always and ``Override size: WxH``
        when a ``wm size <WxH>`` is in effect — the override is what the app sees,
        so that wins; absent it the physical panel size is the effective size.
        """
        try:
            out = self._shell("wm", "size", timeout=5.0)
        except Exception:
            logger.debug("wm size read failed", exc_info=True)
            return ""
        physical = ""
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Override size:"):
                _, _, rhs = s.partition(":")
                return rhs.strip()
            if s.startswith("Physical size:"):
                _, _, rhs = s.partition(":")
                physical = rhs.strip()
        return physical

    def _read_effective_wm_density(self) -> int | None:
        """Effective DPI (override if set, else physical), or ``None`` on parse failure."""
        import contextlib

        try:
            out = self._shell("wm", "density", timeout=5.0)
        except Exception:
            logger.debug("wm density read failed", exc_info=True)
            return None
        physical: int | None = None
        for line in out.splitlines():
            s = line.strip()
            if s.startswith("Override density:"):
                _, _, rhs = s.partition(":")
                try:
                    return int(rhs.strip())
                except ValueError:
                    continue
            if s.startswith("Physical density:"):
                _, _, rhs = s.partition(":")
                with contextlib.suppress(ValueError):
                    physical = int(rhs.strip())
        return physical

    def reset_display_overrides(self) -> None:
        """Clear wm overrides and restore heads-up notifications."""
        self._shell("wm", "size", "reset")
        self._shell("wm", "density", "reset")
        self._screen_resolution = None
        self.set_heads_up_notifications(enabled=True)
        logger.info("Display: wm size/density reset on %s", self._serial)

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
        if self._screen_resolution is not None:
            return self._screen_resolution
        out = self._shell("wm", "size")
        # Override (if set via `wm size WxH`) wins over Physical: screencap also
        # returns the override size, so taps must use the same coordinate space.
        physical: tuple[int, int] | None = None
        override: tuple[int, int] | None = None
        for line in out.splitlines():
            is_override = "Override size:" in line
            is_physical = "Physical size:" in line
            if not (is_override or is_physical):
                continue
            parts = line.split()
            if not parts:
                continue
            w_str, _, h_str = parts[-1].partition("x")
            if not (w_str.isdigit() and h_str.isdigit()):
                continue
            size = (int(w_str), int(h_str))
            if is_override:
                override = size
            else:
                physical = size
        chosen = override or physical
        if chosen is not None:
            self._screen_resolution = chosen
            return chosen
        msg = f"Cannot parse screen resolution from: {out!r}"
        raise RuntimeError(msg)

    def _read_physical_wm_size(self) -> tuple[int, int] | None:
        out = self._shell("wm", "size")
        for line in out.splitlines():
            if "Physical size:" not in line:
                continue
            parts = line.split()
            if not parts:
                continue
            w_str, _, h_str = parts[-1].partition("x")
            if w_str.isdigit() and h_str.isdigit():
                return int(w_str), int(h_str)
        return None
