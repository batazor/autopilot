"""Low-level ADB device controller (tap, swipe, shell, app lifecycle).

``AdbController`` is composed from concern-scoped mixins:

- :class:`adb.controller_display.AdbDisplayMixin` — wm size/density,
  brightness, screen-resolution probing
- :class:`adb.controller_process.AdbProcessMixin` — game-process detection,
  package selection, launch/restart flows
- :class:`adb.controller_input.AdbInputMixin` — tap/swipe/back/type dispatch
- :class:`adb.controller_preview.AdbPreviewMixin` — screenshots, rolling and
  approval previews, approval-slot bookkeeping

This module keeps the construction, device management, and the raw ADB shell
plumbing (``_shell`` / ``_shell_full``) that every mixin builds on. Shared
dataclasses and pure helpers live in :mod:`adb.controller_types` and are
re-exported here for backwards compatibility (``from adb.controller import
ProcessDetection`` etc. keeps working).
"""
from __future__ import annotations

import logging
import subprocess
from typing import TYPE_CHECKING

from adb.controller_display import AdbDisplayMixin
from adb.controller_input import AdbInputMixin
from adb.controller_preview import AdbPreviewMixin
from adb.controller_process import AdbProcessMixin
from adb.controller_types import (
    ProcessDetection as ProcessDetection,
)
from adb.controller_types import (
    _clamp as _clamp,
)
from adb.controller_types import (
    _jitter as _jitter,
)
from adb.controller_types import (
    _jittered_point as _jittered_point,
)
from adb.controller_types import (
    _mentions_package as _mentions_package,
)
from adb.controller_types import (
    _MethodOutcome as _MethodOutcome,
)
from adb.controller_types import (
    _parse_pids as _parse_pids,
)
from adb.controller_types import (
    _ShellOutcome as _ShellOutcome,
)
from adb.controller_types import (
    _tap_offset_spread as _tap_offset_spread,
)
from adb.screencap import (
    DEFAULT_ADB_BIN,
    MSG_ADB_NOT_FOUND,
    resolve_adb_executable,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from adb.scrcpy import ScrcpyClient

logger = logging.getLogger(__name__)


class AdbController(
    AdbDisplayMixin,
    AdbProcessMixin,
    AdbInputMixin,
    AdbPreviewMixin,
):
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
