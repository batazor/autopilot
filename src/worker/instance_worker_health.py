from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

logger = logging.getLogger(__name__)



if TYPE_CHECKING:
    from collections.abc import Callable

    from worker._instance_worker_host import _InstanceWorkerHost as _Base
else:
    _Base = object


class InstanceWorkerHealthMixin(_Base):
    _cfg: Any
    _settings: Any
    _redis: Any
    _stopping: bool
    _blocking_executor_live: bool
    _bot_actions: Any
    _ui_paused: bool
    _startup_pause_reason: str

    async def _run_blocking(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def _set_instance_state(self, state: Any, *, error: str = "") -> None:
        raise NotImplementedError

    async def _cancel_current_task(
        self,
        reason: str,
        *,
        result_reason: str = "aborted_for_restart",
        reschedule: bool = False,
    ) -> bool:
        # Provided by ``InstanceWorker``; declared here so the mixin can call it.
        raise NotImplementedError

    def _ensure_whiteout_at_worker_start(self) -> bool:
        """Block until Whiteout is foreground, launching or restarting as needed.

        Returns ``True`` when the game is verified on a live ADB device. The
        worker must not run overlay analysis or dequeue tasks until this returns
        ``True`` (device-offline and game-not-ready paths set ``_ui_paused``).
        """
        from adb import AdbController, canonical_adb_serial
        from adb.screencap import DEFAULT_ADB_BIN

        self._startup_pause_reason = ""
        ba = self._bot_actions
        inst = self._cfg.instance_id

        adb_bin = (
            (self._settings.worker.adb_executable or "").strip() or DEFAULT_ADB_BIN
        )
        try:
            live = {
                canonical_adb_serial(s) for s in AdbController.list_devices(adb_bin)
            }
        except Exception:
            logger.debug("Startup: adb devices failed for %s", inst, exc_info=True)
            live = set()
        if canonical_adb_serial(self._cfg.bluestacks_window_title) not in live:
            logger.info(
                "Startup: %s device offline (serial=%s) — self-pausing; "
                "watchdog auto-resumes when device returns",
                inst,
                self._cfg.bluestacks_window_title,
            )
            self._ui_paused = True
            self._startup_pause_reason = "device offline (ADB)"
            return False

        try:
            ba.apply_display_then_launch_game(inst, require_approval=False)
        except Exception:
            logger.warning(
                "Startup: display profile / game launch failed for %s — continuing",
                inst,
                exc_info=True,
            )

        timeout_s = max(
            30.0, float(self._settings.worker.game_foreground_timeout_seconds)
        )
        settle_s = 2.5
        deadline = time.monotonic() + timeout_s
        forced_restart = False

        while time.monotonic() < deadline:
            try:
                if ba.is_game_running(inst):
                    logger.info(
                        "Startup: Whiteout process verified running on %s",
                        inst,
                    )
                    return True
            except Exception:
                logger.debug(
                    "Startup: process probe failed on %s", inst, exc_info=True
                )

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            try:
                if not forced_restart and remaining <= timeout_s * 0.5:
                    logger.warning(
                        "Startup: forcing application restart on %s", inst
                    )
                    ba.restart_application(inst)
                    forced_restart = True
                    time.sleep(min(3.0, remaining))
                else:
                    ba.ensure_game_foreground(inst, require_approval=False)
                    time.sleep(min(settle_s, remaining))
            except Exception:
                logger.exception("Startup: launch/restart failed on %s", inst)
                time.sleep(min(settle_s, remaining))

        logger.error(
            "Startup: Whiteout not in foreground on %s within %.0fs — pausing worker",
            inst,
            timeout_s,
        )
        self._ui_paused = True
        self._startup_pause_reason = "game not foreground at startup"
        return False

    _FOREGROUND_VERIFY_TIMEOUT_S = 20.0
    _FOREGROUND_VERIFY_INTERVAL_S = 1.0
    _POST_RESTART_GRACE_S = 10.0

    async def _restart_instance(self) -> None:
        from navigation.lifecycle_states import InstanceState

        logger.warning("Restarting BlueStacks instance %s", self._cfg.instance_id)
        # Pause scenarios + analyzers BEFORE the force-stop. The main loop checks
        # ``_ui_paused`` between tasks (``instance_worker.py``), and the rolling
        # snapshot loop checks it before each capture (``instance_worker_rolling.py``);
        # flipping the flag here stops both for the duration of the restart.
        prev_paused = bool(getattr(self, "_ui_paused", False))
        self._ui_paused = True
        # Kill the in-flight scenario before the force-stop: any remaining tap
        # would land on a dead app / launcher and contaminate state. The
        # cancelled task is recorded as failed in history via ``_execute_task``
        # returning ``TaskResult(success=False, reason="aborted_for_restart")``.
        await self._cancel_current_task("game restart triggered")
        # Yield so the cancellation actually propagates into ``_execute_task``
        # before we begin the blocking ADB calls below.
        await asyncio.sleep(0)
        await self._set_instance_state(InstanceState.RESTARTING)
        await self._redis.delete(f"wos:instance:{self._cfg.instance_id}:lock")

        try:
            try:
                restarted = await self._run_blocking(
                    self._bot_actions.restart_application,
                    self._cfg.instance_id,
                )
                if restarted is False:
                    logger.info(
                        "Restart: %s blocked/rejected by approval",
                        self._cfg.instance_id,
                    )
                    await self._set_instance_state(InstanceState.READY)
                    return
                await asyncio.sleep(3.0)
                await self._run_blocking(
                    self._bot_actions.ensure_game_foreground,
                    self._cfg.instance_id,
                    require_approval=False,
                )
            except Exception:
                logger.exception("Failed to restart application on %s", self._cfg.instance_id)
                await self._set_instance_state(
                    InstanceState.CRASHED, error="restart_application failed (see logs)"
                )
                return

            # Poll until the game process is back (best-effort, bounded).
            # Aliveness, not foreground — the BlueStacks resumed-activity parse
            # would otherwise never confirm and we'd always burn the timeout.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._FOREGROUND_VERIFY_TIMEOUT_S
            while loop.time() < deadline:
                try:
                    is_up = await self._run_blocking(
                        self._bot_actions.is_game_running,
                        self._cfg.instance_id,
                    )
                except Exception:
                    logger.debug(
                        "Restart: is_game_running probe failed on %s",
                        self._cfg.instance_id,
                        exc_info=True,
                    )
                    is_up = False
                if is_up:
                    break
                await asyncio.sleep(self._FOREGROUND_VERIFY_INTERVAL_S)
            else:
                logger.warning(
                    "Restart: %s process did not come back within %.1fs — resuming anyway",
                    self._cfg.instance_id,
                    self._FOREGROUND_VERIFY_TIMEOUT_S,
                )

            logger.info(
                "Restart: %s back in foreground — settling for %.0fs before resume",
                self._cfg.instance_id,
                self._POST_RESTART_GRACE_S,
            )
            await asyncio.sleep(self._POST_RESTART_GRACE_S)

            # A game relaunch is the only moment a runtime account switch can
            # happen (switching characters reloads the game). Clear the cached
            # identity so the next rolling tick re-arms ``who_i_am`` and
            # re-verifies who is logged in. The durable ``last_active_player`` is
            # intentionally kept — the probe overwrites it with the freshly
            # OCR'd id, self-correcting after a switch.
            with contextlib.suppress(Exception):
                await self._redis.hset(
                    f"wos:instance:{self._cfg.instance_id}:state",
                    "active_player",
                    "",
                )

            await self._set_instance_state(InstanceState.READY)
        finally:
            # Restore the previous pause state. If the operator had paused us
            # before the restart event arrived, we don't want to silently resume.
            self._ui_paused = prev_paused
