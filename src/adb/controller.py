"""Low-level ADB device controller (tap, swipe, shell, app lifecycle)."""
from __future__ import annotations

import json
import logging
import os
import random
import shlex
import subprocess
import tempfile
import time
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from adb.approvals import (
    APPROVAL_CURRENT_TTL_SECONDS,
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
    rolling_preview_basename,
    temporal_png_abs_path,
)
from layout.types import Point

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from adb.scrcpy import ScrcpyClient
    from config.device_display import DeviceDisplayConfig

from adb.serial import is_emulator_adb_serial
from config.games import default_game as _default_game
from config.games import game_ids_for_packages as _game_ids_for_packages
from config.games import matching_packages_for_game as _matching_packages_for_game
from config.games import package_for_game as _package_for_game
from config.games import packages_for_game as _packages_for_game

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessDetection:
    """Structured result of a game-process liveness probe.

    ``found`` is the answer; ``pids`` lists every matching process (the main
    process plus sub-processes like ``com.gof.global:render``) when the winning
    method can report PIDs — ``dumpsys``/``am stack`` confirm presence but yield
    no PIDs, so ``pids`` is empty there even when ``found`` is true.
    ``method_used`` is the detection method that produced the verdict
    (``"none"`` if every method failed). ``error`` is ``None`` on a clean
    verdict — including a clean *not running* — and is only set when **every**
    method failed (ADB error/timeout), so callers can tell "process is dead"
    apart from "we could not ask".
    """

    found: bool
    pids: list[int]
    method_used: str
    error: str | None = None


@dataclass(frozen=True)
class _ShellOutcome:
    """Full result of one ADB shell invocation (``rc`` is ``None`` on timeout)."""

    rc: int | None
    stdout: str
    stderr: str

    @property
    def timed_out(self) -> bool:
        return self.rc is None


@dataclass
class _MethodOutcome:
    """Per-method detection result.

    ``error is None`` means the method ran cleanly; ``matched`` is then the
    authoritative found/not-found. ``error`` set means the method itself failed
    (timeout / non-zero rc) and the verdict is unknown — fall through to the
    next method.
    """

    matched: bool = False
    pids: list[int] = field(default_factory=list)
    error: str | None = None


def _parse_pids(text: str) -> list[int]:
    """Extract integer PIDs from whitespace-separated ``pidof`` output."""
    return [int(tok) for tok in text.split() if tok.isdigit()]

# Phase 2: package for the default game (WOS). Phase 2.5 parameterizes the
# methods below so per-profile games select their own package at call time —
# this constant then becomes a fallback for tests / single-game smoke runs.
_GAME_PACKAGE = _package_for_game(_default_game())
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


def _clamp(val: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, val))


def _jitter(value: int, spread: int) -> int:
    """Apply ±spread pixel random jitter."""

    if spread <= 0:
        return value
    return value + random.randint(-spread, spread)


def _jittered_point(
    point: Point,
    *,
    spread: int,
    bounds: tuple[int, int] | None = None,
) -> Point:
    """Apply independent coordinate jitter and keep the result on-screen."""

    x = _jitter(point.x, spread)
    y = _jitter(point.y, spread)
    if bounds is not None:
        w, h = bounds
        x = _clamp(x, 0, max(0, w - 1))
        y = _clamp(y, 0, max(0, h - 1))
    return Point(x, y)


def _tap_offset_spread() -> int:
    """Small per-tap coordinate spread: one run chooses ±1, ±2, or ±3 px."""

    return random.randint(1, 3)


class AdbController:
    """ADB wrapper matching the Go DeviceController interface."""

    def __init__(
        self,
        instance_id: str,
        device_serial: str,
        *,
        adb_bin: str = DEFAULT_ADB_BIN,
        input_backend: str = "",
        scrcpy_client_getter: Callable[[], ScrcpyClient | None] | None = None,
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
        self._screen_resolution: tuple[int, int] | None = None
        # Input backend. Default is "scrcpy" so taps/swipes share the same
        # persistent server as screenshots. Set ``input_backend: adb`` to force
        # Android's shell input path.
        explicit = (input_backend or "").strip().lower()
        self._input_backend = explicit or "scrcpy"
        # scrcpy client is owned by BotActions (one server per device, shared
        # with the screenshot path). The getter returns ``None`` if scrcpy
        # startup has already failed for this device, which is our cue to fall
        # back to ``adb shell input`` instead of trying to start it ourselves.
        self._scrcpy_client_getter = scrcpy_client_getter
        self._verify_available()

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
        msg = (
            f"ADB device '{self._serial}' not found or not in 'device' state.\n"
            f"Connected devices:\n{out}"
        )
        raise RuntimeError(
            msg
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

    # ------------------------------------------------------------------
    # App lifecycle
    # ------------------------------------------------------------------

    def restart_application(self, game: str | None = None) -> None:
        pkg = self._launch_package_for_game(game)
        logger.warning("Restarting %s on %s", pkg, self._serial)
        self._shell("am", "force-stop", pkg)
        time.sleep(2)
        self._shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
        logger.info("Application restarted on %s", self._serial)

    def is_game_running(self, game: str | None = None) -> bool:
        """True if ``game``'s process is alive (regardless of foreground state).

        This is the trustworthy "game is up" signal for the health watchdog. On
        BlueStacks the ``dumpsys`` resumed-activity parse used by
        :meth:`is_game_foreground` is unreliable — the host launcher
        (``com.bluestacks.launcher`` / ``com.uncube.launcher3``) is frequently
        reported as the top activity even while the game runs and renders
        normally. Treating that as "not foreground" force-restarted a perfectly
        healthy game. Process-aliveness does not have that failure mode.

        Thin boolean wrapper over :meth:`detect_game_process`. A detection-level
        failure (every method errored/timed out) is treated as *not running* by
        this wrapper — but unlike a clean miss it is logged at WARNING by the
        detector, so the watchdog's retry loop absorbs a transient blip without
        a false restart while a persistent ADB outage still surfaces.
        """
        return self.detect_game_process(game).found

    def detect_game_process(self, game: str | None = None) -> ProcessDetection:
        """Resilient multi-method process probe for ``game``.

        Tries detection methods in turn and stops at the first that *positively*
        finds the process. On BlueStacks ``pidof`` returns rc=1 even for a live
        game (it is not a clean Android userspace), so the ``ps`` scan leads for
        emulator serials and ``pidof`` is demoted to a last resort; on real
        devices ``pidof`` is fast and reliable, so it leads there.

        Method order:
          - ``ps -A`` (falls back to ``ps``) — parses PIDs, matches the package
            and any sub-process (``<pkg>:render`` etc.); the only method that
            returns PIDs for partial names.
          - ``dumpsys activity recents`` — presence check, no PIDs.
          - ``am stack list`` — presence check, no PIDs (removed on newer
            Android; a non-zero rc there is treated as a method failure, not a
            "not found", so we fall through cleanly).
          - ``pidof`` — exact main-process PID.

        Returns a :class:`ProcessDetection`. ``error`` is only populated when
        *every* method failed, distinguishing "process is dead" (clean rc, no
        match) from "we could not ask" (ADB error/timeout) — see requirement on
        not conflating pidof's overloaded rc=1.
        """
        packages = _packages_for_game(game or _default_game())
        errors: list[str] = []
        last_clean: ProcessDetection | None = None
        for pkg in packages:
            detection = self._detect_game_process_for_package(pkg)
            if detection.found:
                return detection
            if detection.error is not None:
                errors.append(f"{pkg}: {detection.error}")
            else:
                last_clean = detection

        if last_clean is not None:
            return last_clean
        err = "; ".join(errors) or "no detection method available"
        return ProcessDetection(found=False, pids=[], method_used="none", error=err)

    def _detect_game_process_for_package(self, pkg: str) -> ProcessDetection:
        prefer_ps = is_emulator_adb_serial(self._serial)

        methods: list[tuple[str, Callable[[str], _MethodOutcome]]] = [
            ("ps", self._detect_via_ps),
            ("dumpsys_recents", self._detect_via_recents),
            ("am_stack", self._detect_via_am_stack),
            ("pidof", self._detect_via_pidof),
        ]
        if not prefer_ps:
            # Clean Android: pidof first (fast + reliable), rest as fallback.
            methods = [methods[3], *methods[:3]]

        errors: list[str] = []
        last_clean_method: str | None = None
        for name, fn in methods:
            try:
                outcome = fn(pkg)
            except Exception as exc:  # defensive: a parser bug must not kill the probe
                outcome = _MethodOutcome(error=f"unexpected {exc!r}")

            if outcome.error is not None:
                logger.debug(
                    "process-detect[%s]: %s failed for %s: %s",
                    self._serial, name, pkg, outcome.error,
                )
                errors.append(f"{name}: {outcome.error}")
                continue

            if outcome.matched:
                pids = sorted(set(outcome.pids))
                logger.debug(
                    "process-detect[%s]: %s found %s (pids=%s)",
                    self._serial, name, pkg, pids,
                )
                return ProcessDetection(
                    found=True, pids=pids, method_used=name, error=None
                )

            # Clean run, no match — authoritative "not running" for this method,
            # but keep trying: a later method may catch a sub-process this one
            # cannot see (e.g. pidof misses <pkg>:render).
            logger.debug(
                "process-detect[%s]: %s reports %s not running",
                self._serial, name, pkg,
            )
            last_clean_method = name

        if last_clean_method is not None:
            # At least one method ran cleanly and saw nothing → genuinely dead.
            return ProcessDetection(
                found=False, pids=[], method_used=last_clean_method, error=None
            )

        # Every method failed — unknown, not "dead". Warn once (req. 7).
        err = "; ".join(errors) or "no detection method available"
        logger.warning(
            "process-detect[%s]: all methods failed for %s — %s",
            self._serial, pkg, err,
        )
        return ProcessDetection(found=False, pids=[], method_used="none", error=err)

    def _detect_via_pidof(self, pkg: str) -> _MethodOutcome:
        out = self._shell_full("pidof", pkg, timeout=5.0)
        if out.timed_out:
            return _MethodOutcome(error="timed out")
        pids = _parse_pids(out.stdout)
        if out.rc == 0:
            return _MethodOutcome(matched=bool(pids), pids=pids)
        # pidof's rc=1 is overloaded: "not found" AND some error states. Treat a
        # silent rc=1 (no stderr) as a clean miss; anything noisier is a failure.
        if out.rc == 1 and not out.stderr:
            return _MethodOutcome(matched=False)
        return _MethodOutcome(error=f"rc={out.rc} {out.stderr}".strip())

    def _detect_via_ps(self, pkg: str) -> _MethodOutcome:
        out = self._shell_full("ps", "-A", timeout=5.0)
        if out.timed_out:
            return _MethodOutcome(error="timed out (ps -A)")
        if out.rc != 0:
            # Legacy toybox/BusyBox images reject ``-A``; bare ``ps`` lists all.
            out = self._shell_full("ps", timeout=5.0)
            if out.timed_out:
                return _MethodOutcome(error="timed out (ps)")
            if out.rc != 0:
                return _MethodOutcome(error=f"rc={out.rc} {out.stderr}".strip())
        return self._match_ps_pids(out.stdout, pkg)

    def _match_ps_pids(self, stdout: str, pkg: str) -> _MethodOutcome:
        """Parse ``ps`` output: PID is column 2, process name is the last column.

        Matches the package itself and Android sub-processes named ``<pkg>:tag``
        (e.g. ``com.gof.global:render``) — but not unrelated ``<pkg>foo``.
        """
        pids: list[int] = []
        for line in stdout.splitlines():
            parts = line.split()
            if len(parts) < 2 or not parts[1].isdigit():
                continue  # header ("USER PID ...") or malformed row
            name = parts[-1]
            if name == pkg or name.startswith(f"{pkg}:"):
                pids.append(int(parts[1]))
        return _MethodOutcome(matched=bool(pids), pids=pids)

    def _detect_via_recents(self, pkg: str) -> _MethodOutcome:
        out = self._shell_full("dumpsys", "activity", "recents", timeout=10.0)
        if out.timed_out:
            return _MethodOutcome(error="timed out")
        if out.rc != 0:
            return _MethodOutcome(error=f"rc={out.rc} {out.stderr}".strip())
        matched = any(pkg in line for line in out.stdout.splitlines())
        return _MethodOutcome(matched=matched)

    def _detect_via_am_stack(self, pkg: str) -> _MethodOutcome:
        out = self._shell_full("am", "stack", "list", timeout=5.0)
        if out.timed_out:
            return _MethodOutcome(error="timed out")
        if out.rc != 0:
            return _MethodOutcome(error=f"rc={out.rc} {out.stderr}".strip())
        matched = any(pkg in line for line in out.stdout.splitlines())
        return _MethodOutcome(matched=matched)

    def is_game_foreground(self, game: str | None = None) -> bool:
        """True if ``game``'s process is alive and is the resumed foreground activity."""
        game_id = game or _default_game()
        packages = _packages_for_game(game_id)
        # Fast check: is the process even alive?
        if not any(self._detect_game_process_for_package(pkg).found for pkg in packages):
            logger.debug("is_game_foreground: no PID for %s — process dead", game_id)
            return False

        # Foreground check: dumpsys activity stack
        out = self._shell("dumpsys", "activity", "activities", timeout=10.0)
        markers = ("topResumedActivity=", "ResumedActivity:", "mResumedActivity:")
        for line in out.splitlines():
            if not any(pkg in line for pkg in packages):
                continue
            s = line.strip()
            if any(m in s for m in markers):
                return True
        return False

    def ensure_game_foreground(self, game: str | None = None) -> None:
        """Start ``game`` if it isn't the foreground resumed activity."""
        pkg = self._launch_package_for_game(game)
        if self.is_game_foreground(game):
            logger.info("Game already foreground (%s on %s)", pkg, self._serial)
            return
        logger.warning(
            "Game not in foreground — launching %s on %s", pkg, self._serial
        )
        self._shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(2)

    def list_installed_games(self) -> list[str]:
        """Game ids whose packages are installed on the device.

        Used by the ``/adb`` UI dropdown to filter selectable games to those
        the user actually has on the emulator. Empty on offline devices.
        """
        return _game_ids_for_packages(self._installed_packages())

    def _installed_game_packages(self, game: str | None = None) -> list[str]:
        """Installed Android package ids for ``game``, preserving registry order."""
        return list(
            _matching_packages_for_game(
                game or _default_game(),
                self._installed_packages(),
            )
        )

    def _installed_packages(self) -> set[str]:
        """Android package ids installed on the active device."""
        out = self._shell("pm", "list", "packages", timeout=10.0)
        return {
            line.removeprefix("package:").strip()
            for line in out.splitlines()
            if line.strip()
        }

    def _running_package_for_game(self, game: str | None = None) -> str | None:
        """Running Android package id for ``game``, including aliases."""
        for pkg in _packages_for_game(game or _default_game()):
            if self._detect_game_process_for_package(pkg).found:
                return pkg
        return None

    def _launch_package_for_game(self, game: str | None = None) -> str:
        """Best package to launch/restart for ``game`` on this device.

        Prefer the package already running (e.g. WOS beta), then an installed
        alias, then the canonical package as a final default.
        """
        game_id = game or _default_game()
        running_package = self._running_package_for_game(game_id)
        if running_package is not None:
            return running_package

        installed_packages = self._installed_game_packages(game_id)
        if installed_packages:
            return installed_packages[0]

        return _package_for_game(game_id)

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
        if click_approval_enabled(self._instance_id) and src != "navigation":
            self._attach_approval_preview(ap)
            ap["_preview_capturer"] = self._attach_approval_preview
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
            tmp.replace(path)
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
            path = temporal_png_abs_path(
                root,
                rolling_preview_basename(self._instance_id),
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
            rolling_path = temporal_png_abs_path(
                root,
                rolling_preview_basename(self._instance_id),
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
        rel = approval_path.relative_to(root)
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
                "Failed to refresh rolling preview for %s", self._instance_id
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
                doc = json.loads(raw)  # ty: ignore[invalid-argument-type]
                doc["executed_at"] = time.time()
                doc["status"] = "executing"
                _redis().set(
                    current_key,
                    json.dumps(doc),
                    ex=APPROVAL_CURRENT_TTL_SECONDS,
                )
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
                [self._adb_exe, "-s", self._serial, "shell", *list(args)],
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

    def _shell_full(self, *args: str, timeout: float = 15.0) -> _ShellOutcome:
        """Like :meth:`_shell` but preserves rc/stderr and timeout vs failure.

        :meth:`_shell` collapses timeout, non-zero exit, and success-with-empty
        output all to ``""`` — fine for fire-and-forget commands, useless when
        the caller must tell "process not found" (clean rc) from "the call
        failed" (timeout / error rc). Used by :meth:`detect_game_process`; logs
        nothing itself so the detector owns the per-method DEBUG/WARNING policy.
        """
        try:
            result = subprocess.run(
                [self._adb_exe, "-s", self._serial, "shell", *list(args)],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return _ShellOutcome(rc=None, stdout="", stderr="")
        return _ShellOutcome(
            rc=result.returncode,
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
        )


# ---------------------------------------------------------------------------
# BotActions — instance-aware facade used by tasks and the use case executor
# ---------------------------------------------------------------------------
