from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

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

    async def _health_check(self) -> bool:
        """ADB-only: ``dumpsys`` must report Whiteout as resumed foreground activity."""
        try:
            fg = await self._run_blocking(
                self._bot_actions.is_game_foreground,
                self._cfg.instance_id,
            )
        except Exception:
            logger.exception(
                "Health check: is_game_foreground (ADB) failed for %s",
                self._cfg.instance_id,
            )
            return False

        if not fg:
            logger.warning(
                "Health check: Whiteout not foreground on %s — scheduling app restart",
                self._cfg.instance_id,
            )
            return False
        return True

    def _ensure_whiteout_at_worker_start(self) -> None:
        from actions.tap import BotActions

        BotActions().ensure_game_foreground(self._cfg.instance_id)

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

