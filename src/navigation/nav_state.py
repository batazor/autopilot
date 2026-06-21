"""Redis persistence for per-instance navigation screen state.

Extracted from :class:`navigation.navigator.Navigator` so the Redis key schema
and read/write/exception-handling details live in one testable collaborator
instead of being interleaved with routing logic. ``Navigator`` owns a
``NavStateStore`` and forwards its state-IO methods to it.

State hash ``wos:instance:<id>:state`` fields written here:
``current_screen``, ``nav_expected_screen``, ``nav_error``. Rolling
most-recent-first screen history lives in the ``…:screen_history`` list.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Rolling screen-history depth (index 0 = current screen).
SCREEN_HISTORY_MAX = 5


class NavStateStore:
    """Reads/writes the navigation slice of an instance's Redis state.

    Every method is a no-op (or returns an empty default) when Redis is absent,
    and swallows transport errors with a debug log — navigation must never crash
    because a state write failed.
    """

    def __init__(self, redis: Any | None) -> None:
        self._redis = redis

    def state_key(self, instance_id: str) -> str:
        return f"wos:instance:{instance_id}:state"

    def history_key(self, instance_id: str) -> str:
        return f"wos:instance:{instance_id}:screen_history"

    async def set_expected_screen(self, instance_id: str, screen: str) -> None:
        """Publish the hop/scenario target so the worker probes it first each tick."""
        if self._redis is None:
            return
        value = str(screen or "").strip()
        try:
            await self._redis.hset(
                self.state_key(instance_id), "nav_expected_screen", value
            )
        except Exception:
            logger.debug(
                "NavStateStore: failed to write nav_expected_screen to Redis",
                exc_info=True,
            )

    async def clear_expected_screen(self, instance_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hdel(self.state_key(instance_id), "nav_expected_screen")
        except Exception:
            logger.debug(
                "NavStateStore: failed to clear nav_expected_screen in Redis",
                exc_info=True,
            )

    async def write_screen(self, instance_id: str, screen: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hset(
                self.state_key(instance_id), "current_screen", screen
            )
        except Exception:
            logger.debug(
                "NavStateStore: failed to write current_screen to Redis", exc_info=True
            )
        # Push to rolling history. Skip empty strings (those represent "unknown"
        # after a verify failure — a history entry there would suggest the bot
        # was on a real "unknown" screen, which confuses ``from_screen`` rules
        # that look one hop back). De-dupe consecutive duplicates so navigating
        # back to a screen we were already on doesn't push a useless repeat.
        screen_s = str(screen or "").strip()
        if not screen_s:
            return
        try:
            head = await self._redis.lindex(self.history_key(instance_id), 0)
            head_s = (head.decode() if isinstance(head, bytes) else str(head or "")).strip()
            if head_s == screen_s:
                return
            await self._redis.lpush(self.history_key(instance_id), screen_s)
            await self._redis.ltrim(
                self.history_key(instance_id), 0, SCREEN_HISTORY_MAX - 1
            )
        except Exception:
            logger.debug(
                "NavStateStore: failed to push screen history to Redis", exc_info=True
            )

    async def write_error(self, instance_id: str, detail: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hset(
                self.state_key(instance_id), "nav_error", str(detail or "").strip()
            )
        except Exception:
            logger.debug(
                "NavStateStore: failed to write nav_error to Redis", exc_info=True
            )

    async def clear_error(self, instance_id: str) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.hset(self.state_key(instance_id), "nav_error", "")
        except Exception:
            logger.debug(
                "NavStateStore: failed to clear nav_error in Redis", exc_info=True
            )

    async def screen_history(self, instance_id: str) -> list[str]:
        """Most-recent-first list of screens previously written for this instance.

        Index 0 is the current screen; index 1 the one before, and so on. Empty
        list when Redis is absent or the key was never populated.
        """
        if self._redis is None:
            return []
        try:
            raw = await self._redis.lrange(
                self.history_key(instance_id), 0, SCREEN_HISTORY_MAX - 1
            )
        except Exception:
            logger.debug(
                "NavStateStore: failed to read screen history from Redis", exc_info=True
            )
            return []
        out: list[str] = []
        for item in raw or []:
            s = (item.decode() if isinstance(item, bytes) else str(item or "")).strip()
            if s:
                out.append(s)
        return out
