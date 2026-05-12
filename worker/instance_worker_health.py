from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class InstanceWorkerHealthMixin:
    _cfg: Any
    _redis: Any
    _stopping: bool
    _blocking_executor_live: bool
    _bot_actions: Any

    async def _run_blocking(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def _set_instance_state(self, state: Any, *, error: str = "") -> None:
        raise NotImplementedError

    def _ensure_whiteout_at_worker_start(self) -> None:
        """Bring Whiteout to foreground and verify process + resumed activity (same as health check)."""
        from actions.tap import AdbController, BotActions, canonical_adb_serial
        from capture.adb_screencap import DEFAULT_ADB_BIN

        ba = BotActions()
        inst = self._cfg.instance_id

        # Pre-flight: if our ADB serial isn't connected right now, skip the
        # whole startup foreground dance. ``AdbController.__init__`` would
        # otherwise raise ``device not found`` and spam a startup traceback
        # every worker reboot, even though ``game_health_watchdog`` will
        # auto-pause the instance within ``health_check_interval_seconds``.
        from config.loader import get_settings

        adb_bin = (
            (get_settings().worker.adb_executable or "").strip() or DEFAULT_ADB_BIN
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
            # Flip the same flag the operator pause does so the main loop's
            # ``while self._ui_paused`` gate stops any queued task from
            # touching ADB. The async caller in ``run()`` mirrors this to
            # Redis state so the UI shows "paused (auto)" right away —
            # otherwise we race the watchdog by up to ``health_check_interval``.
            self._ui_paused = True
            return

        attempts = 5
        settle_s = 2.5

        for n in range(1, attempts + 1):
            ba.ensure_game_foreground(inst)
            time.sleep(settle_s)
            if ba.is_game_foreground(inst):
                logger.info(
                    "Startup: Whiteout verified running and foreground on %s (%d/%d)",
                    inst,
                    n,
                    attempts,
                )
                return
            logger.warning(
                "Startup: Whiteout not verified on %s — retry %d/%d",
                inst,
                n,
                attempts,
            )

        logger.warning(
            "Startup: forcing application restart on %s after failed verification",
            inst,
        )
        try:
            ba.restart_application(inst)
            time.sleep(3.0)
            ba.ensure_game_foreground(inst)
            time.sleep(settle_s)
            if ba.is_game_foreground(inst):
                logger.info("Startup: Whiteout verified on %s after forced restart", inst)
                return
        except Exception:
            logger.exception("Startup: forced restart failed on %s", inst)

        logger.error(
            "Startup: Whiteout could not be verified on %s — worker will still start",
            inst,
        )

    async def _restart_instance(self) -> None:
        from fsm.states import InstanceState

        logger.warning("Restarting BlueStacks instance %s", self._cfg.instance_id)
        await self._set_instance_state(InstanceState.RESTARTING)
        await self._redis.delete(f"wos:instance:{self._cfg.instance_id}:lock")

        try:
            self._bot_actions.restart_application(self._cfg.instance_id)
            await asyncio.sleep(3.0)
            await self._run_blocking(
                self._bot_actions.ensure_game_foreground,
                self._cfg.instance_id,
            )
        except Exception:
            logger.exception("Failed to restart application on %s", self._cfg.instance_id)
            await self._set_instance_state(
                InstanceState.CRASHED, error="restart_application failed (see logs)"
            )
            return

        await self._set_instance_state(InstanceState.READY)

