"""Process detection and app lifecycle for :class:`adb.controller.AdbController`."""
from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING

from adb.approvals import (
    _consume_skip,
    _redis,
    _require_approval,
)
from adb.controller_types import (
    ProcessDetection,
    _mentions_package,
    _MethodOutcome,
    _parse_pids,
)
from adb.serial import is_emulator_adb_serial
from config.games import default_game as _default_game
from config.games import game_for_package as _game_for_package
from config.games import game_ids_for_packages as _game_ids_for_packages
from config.games import iter_games as _iter_games
from config.games import matching_packages_for_game as _matching_packages_for_game
from config.games import module_catalog_for_package as _module_catalog_for_package
from config.games import package_for_game as _package_for_game
from config.games import packages_for_game as _packages_for_game

if TYPE_CHECKING:
    from collections.abc import Callable

if TYPE_CHECKING:
    from adb._controller_host import _ControllerHost as _Base
else:
    _Base = object

logger = logging.getLogger(__name__)

# Phase 2: package for the default game (WOS). Phase 2.5 parameterizes the
# methods below so per-profile games select their own package at call time —
# this constant then becomes a fallback for tests / single-game smoke runs.
_GAME_PACKAGE = _package_for_game(_default_game())
_LAUNCHER_ACTION = "android.intent.action.MAIN"
_LAUNCHER_CATEGORY = "android.intent.category.LAUNCHER"
_STATE_KEY_FMT = "wos:instance:{instance_id}:state"
_LAST_GAME_ID_FIELD = "last_game_id"
_LAST_GAME_PACKAGE_FIELD = "last_game_package"
_LAST_GAME_PACKAGE_AT_FIELD = "last_game_package_at"
_LAST_GAME_PACKAGE_SOURCE_FIELD = "last_game_package_source"
# "1" when the running package is a beta/alias build (not the canonical store
# package). Century-backed flows (identity sync, gift codes) can't see beta
# accounts, so scenarios gate on this field to skip them on beta instances.
_LAST_GAME_IS_BETA_FIELD = "last_game_is_beta"


class AdbProcessMixin(_Base):
    """Game-process probes, package selection, and launch/restart flows."""

    # ------------------------------------------------------------------
    # App lifecycle
    # ------------------------------------------------------------------

    def restart_application(self, game: str | None = None) -> bool:
        pkg = self._launch_package_for_game(game)
        payload = self._approval_payload_with_preview(
            {
                "type": "restart_application",
                "package": pkg,
                "serial": self._serial,
            }
        )
        ok, req_id = _require_approval(self._instance_id, payload)
        if not ok:
            logger.info(
                "ADB restart blocked (no approval): %s package=%s",
                self._instance_id,
                pkg,
            )
            return False
        if _consume_skip(req_id):
            logger.info(
                "ADB restart skipped by operator: %s package=%s",
                self._instance_id,
                pkg,
            )
            self._refresh_rolling_preview()
            return False
        logger.warning("Restarting %s on %s", pkg, self._serial)
        with self._approval_execution(req_id):
            self._shell("am", "force-stop", pkg)
            time.sleep(2)
            self._start_launcher_activity(pkg)
            if self._detect_live_process_for_package(pkg):
                self._remember_game_package(game or _default_game(), pkg, source="launch")
            self._refresh_rolling_preview()
        logger.info("Application restarted on %s", self._serial)
        return True

    def is_game_running(self, game: str | None = None) -> bool:
        """True if ``game``'s selected package process is alive.

        This is the trustworthy "game is up" signal for the health watchdog. On
        BlueStacks the ``dumpsys`` resumed-activity parse used by
        :meth:`is_game_foreground` is unreliable — the host launcher
        (``com.bluestacks.launcher`` / ``com.uncube.launcher3``) is frequently
        reported as the top activity even while the game runs and renders
        normally. Treating that as "not foreground" force-restarted a perfectly
        healthy game. Process-aliveness does not have that failure mode.

        Thin boolean wrapper over a process-only package scan. If a supported
        alias is the package that is actually running, that alias satisfies
        liveness and becomes the restart/foreground target.
        """
        game_id = game or _default_game()
        return self._running_package_for_game(game_id) is not None

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

    def _state_key(self) -> str:
        return _STATE_KEY_FMT.format(instance_id=self._instance_id)

    def _remember_game_package(
        self,
        game: str,
        pkg: str,
        *,
        source: str,
    ) -> None:
        """Persist the package that most recently represented ``game``."""

        game_id = game or _default_game()
        if pkg not in _packages_for_game(game_id):
            return
        # Canonical store package is the first entry of ``packages_for_game``;
        # anything else is an accepted alias (a beta build).
        is_beta = "1" if pkg != _package_for_game(game_id) else "0"
        try:
            from services import bind_active_module_catalog

            bind_active_module_catalog(_module_catalog_for_package(game_id, pkg))
        except Exception:
            logger.debug(
                "module-catalog bind failed for %s package=%s",
                self._instance_id,
                pkg,
                exc_info=True,
            )
        try:
            _redis().hset(
                self._state_key(),
                mapping={
                    _LAST_GAME_ID_FIELD: game_id,
                    _LAST_GAME_PACKAGE_FIELD: pkg,
                    _LAST_GAME_PACKAGE_AT_FIELD: f"{time.time():.3f}",
                    _LAST_GAME_PACKAGE_SOURCE_FIELD: source,
                    _LAST_GAME_IS_BETA_FIELD: is_beta,
                },
            )
        except Exception:
            logger.debug(
                "game-package memory write failed for %s package=%s",
                self._instance_id,
                pkg,
                exc_info=True,
            )

    def _detect_live_process_for_package(self, pkg: str) -> bool:
        """Process-only liveness check used for launch target selection.

        ``detect_game_process`` intentionally accepts Android recents / stack
        evidence as a soft liveness signal for BlueStacks health checks. Launch
        selection needs a stricter answer: if only recents mention a package,
        that package is a candidate to launch, not proof that it is running.
        """

        methods: list[Callable[[str], _MethodOutcome]] = [
            self._detect_via_ps,
            self._detect_via_pidof,
        ]
        if not is_emulator_adb_serial(self._serial):
            methods.reverse()
        for fn in methods:
            try:
                outcome = fn(pkg)
            except Exception:
                logger.debug(
                    "live-process probe failed for %s on %s",
                    pkg,
                    self._serial,
                    exc_info=True,
                )
                continue
            if outcome.error is None and outcome.matched:
                return True
        return False

    def _foreground_package_for_game(
        self,
        game: str,
        target_packages: list[str],
    ) -> str | None:
        """Package from ``target_packages`` currently in the resumed activity."""

        if not target_packages:
            return None
        try:
            out = self._shell("dumpsys", "activity", "activities", timeout=10.0)
        except Exception:
            logger.debug("foreground package probe failed on %s", self._serial, exc_info=True)
            return None
        markers = ("topResumedActivity=", "ResumedActivity:", "mResumedActivity:")
        for line in out.splitlines():
            s = line.strip()
            if not any(m in s for m in markers):
                continue
            pkg = next((p for p in target_packages if _mentions_package(s, p)), "")
            if pkg:
                self._remember_game_package(game, pkg, source="foreground")
                return pkg
        return None

    def _remembered_game_package(
        self,
        game: str,
        target_packages: list[str],
    ) -> str | None:
        game_id = game or _default_game()
        try:
            state = _redis().hgetall(self._state_key())
        except Exception:
            logger.debug(
                "game-package memory read failed for %s",
                self._instance_id,
                exc_info=True,
            )
            return None
        if not isinstance(state, dict):
            return None
        remembered_game = str(state.get(_LAST_GAME_ID_FIELD) or game_id).strip()
        pkg = str(state.get(_LAST_GAME_PACKAGE_FIELD) or "").strip()
        if remembered_game != game_id:
            return None
        if pkg not in target_packages:
            return None
        return pkg

    def _recent_package_for_game(
        self,
        game: str,
        target_packages: list[str],
    ) -> str | None:
        """Most recent package from Android's task recents, if any."""

        if not target_packages:
            return None
        out = self._shell_full("dumpsys", "activity", "recents", timeout=10.0)
        if out.timed_out or out.rc != 0:
            return None
        for line in out.stdout.splitlines():
            for pkg in target_packages:
                if _mentions_package(line, pkg):
                    self._remember_game_package(game, pkg, source="recents")
                    return pkg
        return None

    def is_game_foreground(self, game: str | None = None) -> bool:
        """True if ``game``'s process is alive and is the resumed foreground activity."""
        game_id = game or _default_game()
        packages = self._target_packages_for_game(game_id)
        return self._foreground_package_for_game(game_id, packages) is not None

    def current_foreground_activity(self) -> str:
        """Best-effort ``pkg/activity`` of the resumed foreground app (or "").

        Records *what was on screen* when the watchdog decided the game was
        dead — a launcher/other-app component points at a real crash, while the
        game's own component points at a detection flake. Never raises.
        """
        markers = ("topResumedActivity=", "ResumedActivity:", "mResumedActivity:")
        try:
            out = self._shell("dumpsys", "activity", "activities", timeout=10.0)
        except Exception:
            logger.debug("current_foreground_activity: dumpsys failed on %s", self._serial)
            return ""
        for line in out.splitlines():
            s = line.strip()
            if any(m in s for m in markers):
                match = re.search(r"[A-Za-z0-9_.]+/[A-Za-z0-9_.]+", s)
                if match:
                    return match.group(0)
        return ""

    def current_foreground_game(self) -> str | None:
        """Known game id of the resumed foreground app, or ``None``.

        Reverse-looks-up the resumed activity's package against the game
        registry. Returns ``None`` when the launcher / an unrelated app is
        foreground (or the probe fails) — i.e. when nothing recognizable is
        on screen.
        """
        activity = self.current_foreground_activity()
        pkg = activity.split("/", 1)[0].strip() if activity else ""
        return _game_for_package(pkg) if pkg else None

    def detect_running_game(self) -> str | None:
        """Known game actually live on this device, or ``None``.

        Prefers the resumed foreground game; if nothing recognizable is
        foreground (e.g. a transient launcher frame during boot) it falls back
        to the first known game with a live process. ``None`` means no known
        game is running — callers should then use the configured game.
        """
        foreground = self.current_foreground_game()
        if foreground is not None:
            return foreground
        for game_id in _iter_games():
            if self._running_package_for_game(game_id) is not None:
                return game_id
        return None

    def ensure_game_foreground(
        self,
        game: str | None = None,
        *,
        require_approval: bool = True,
    ) -> bool:
        """Start ``game`` if it isn't the foreground resumed activity."""
        pkg = self._launch_package_for_game(game)
        if self.is_game_foreground(game):
            logger.info("Game already foreground (%s on %s)", pkg, self._serial)
            return True
        req_id: str | None = None
        if require_approval:
            payload = self._approval_payload_with_preview(
                {
                    "type": "ensure_game_foreground",
                    "package": pkg,
                    "serial": self._serial,
                }
            )
            ok, req_id = _require_approval(self._instance_id, payload)
            if not ok:
                logger.info(
                    "ADB foreground launch blocked (no approval): %s package=%s",
                    self._instance_id,
                    pkg,
                )
                return False
            if _consume_skip(req_id):
                logger.info(
                    "ADB foreground launch skipped by operator: %s package=%s",
                    self._instance_id,
                    pkg,
                )
                self._refresh_rolling_preview()
                return False
        logger.warning(
            "Game not in foreground — launching %s on %s", pkg, self._serial
        )
        with self._approval_execution(req_id):
            self._start_launcher_activity(pkg)
            time.sleep(2)
            if self._detect_live_process_for_package(pkg):
                self._remember_game_package(game or _default_game(), pkg, source="launch")
            self._refresh_rolling_preview()
        return True

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

    def _target_packages_for_game(
        self,
        game: str,
        installed_packages: list[str] | None = None,
    ) -> list[str]:
        """Launch/check candidates for ``game``, preserving configured package order."""

        game_id = game or _default_game()
        if installed_packages:
            return list(installed_packages)
        return list(_packages_for_game(game_id))

    def _running_package_for_game(
        self,
        game: str | None = None,
        target_packages: list[str] | None = None,
    ) -> str | None:
        """Live Android process package id for ``game``."""

        game_id = game or _default_game()
        packages = target_packages or self._target_packages_for_game(game_id)
        for pkg in packages:
            if self._detect_live_process_for_package(pkg):
                self._remember_game_package(game_id, pkg, source="process")
                return pkg
        return None

    def _launch_package_for_game(self, game: str | None = None) -> str:
        """Package to launch/restart for ``game`` on this device.

        The package already foreground/running wins. If nothing is running,
        Redis and Android recents preserve the last package variant seen on this
        instance. Only when there is no runtime/recent signal do we fall back to
        the configured canonical package.
        """

        game_id = game or _default_game()
        installed_packages = self._installed_game_packages(game_id)
        target_packages = self._target_packages_for_game(game_id, installed_packages)

        foreground_package = self._foreground_package_for_game(game_id, target_packages)
        if foreground_package is not None:
            return foreground_package

        running_package = self._running_package_for_game(game_id, target_packages)
        if running_package is not None:
            return running_package

        remembered_package = self._remembered_game_package(game_id, target_packages)
        if remembered_package is not None:
            return remembered_package

        recent_package = self._recent_package_for_game(game_id, target_packages)
        if recent_package is not None:
            return recent_package

        if installed_packages:
            return installed_packages[0]

        return _package_for_game(game_id)

    def _launcher_component_for_package(self, pkg: str) -> str:
        out = self._shell(
            "cmd",
            "package",
            "resolve-activity",
            "-a",
            _LAUNCHER_ACTION,
            "-c",
            _LAUNCHER_CATEGORY,
            "-p",
            pkg,
            "--brief",
            timeout=10.0,
        )
        for line in reversed(out.splitlines()):
            s = line.strip()
            if "/" in s and _mentions_package(s, pkg):
                return s
        return ""

    def _start_launcher_activity(self, pkg: str) -> None:
        """Bring ``pkg`` to the foreground via its normal launcher intent."""
        component = self._launcher_component_for_package(pkg)
        if component:
            self._shell("am", "start", "-n", component, timeout=10.0)
            return
        self._shell(
            "monkey",
            "-p",
            pkg,
            "-c",
            _LAUNCHER_CATEGORY,
            "1",
            timeout=10.0,
        )
