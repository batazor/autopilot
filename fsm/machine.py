from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Coroutine
from typing import Any

import redis.asyncio as aioredis
from transitions import Machine  # type: ignore[import-untyped]

from fsm.states import PLAYER_TRANSITIONS, PlayerState

logger = logging.getLogger(__name__)


class PlayerFSM:
    """FSM for one player account; persists state to Redis on every transition."""

    def __init__(
        self,
        player_id: str,
        redis_client: aioredis.Redis,  # type: ignore[type-arg]
        *,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self.player_id = player_id
        self._redis = redis_client
        self._redis_key = f"wos:player:{player_id}:state"
        if loop is not None:
            self._loop = loop
        else:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
                logger.warning(
                    "PlayerFSM(%s): no running event loop; Redis persist/restart signals disabled",
                    player_id,
                )

        self.machine = Machine(
            model=self,
            states=list(PlayerState),
            transitions=PLAYER_TRANSITIONS,
            initial=PlayerState.IDLE,
            after_state_change=self._persist_state,
        )

    def _task_done(self, task: asyncio.Task[Any]) -> None:
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            logger.error(
                "FSM background task failed for player %s",
                self.player_id,
                exc_info=exc,
            )

    def _schedule_coro(self, coro: Coroutine[Any, Any, None]) -> None:
        """Run coroutine on the worker's loop (FSM callbacks are synchronous)."""
        if self._loop is None:
            logger.error(
                "Player %s: cannot schedule FSM coroutine (no event loop)",
                self.player_id,
            )
            coro.close()
            return
        task = self._loop.create_task(coro)
        task.add_done_callback(self._task_done)

    def _persist_state(self) -> None:
        self._schedule_coro(self._async_persist_state())

    async def _async_persist_state(self) -> None:
        await self._redis.hset(self._redis_key, "fsm_state", self.state)  # type: ignore[attr-defined]
        logger.debug("Player %s FSM state → %s", self.player_id, self.state)
        hist_key = f"wos:player:{self.player_id}:fsm_history"
        entry = json.dumps({"ts": time.time(), "state": self.state})
        await self._redis.lpush(hist_key, entry)  # type: ignore[attr-defined]
        await self._redis.ltrim(hist_key, 0, 19)  # type: ignore[attr-defined]

    async def restore_from_redis(self) -> None:
        raw = await self._redis.hget(self._redis_key, "fsm_state")
        if raw:
            saved_state = raw.decode() if isinstance(raw, bytes) else raw
            if saved_state in list(PlayerState):
                self.machine.set_state(saved_state)

    def on_enter_recovering(self) -> None:
        logger.warning("Player %s entering recovery", self.player_id)

    def on_enter_game_closed(self) -> None:
        logger.error("Player %s game closed — requesting restart", self.player_id)
        self._schedule_coro(self._publish_restart_signal())

    async def _publish_restart_signal(self) -> None:
        await self._redis.publish(
            "wos:events:restart",
            f'{{"player_id": "{self.player_id}"}}',
        )
