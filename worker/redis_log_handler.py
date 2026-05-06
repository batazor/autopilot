"""Push log lines to Redis list for Streamlit UI (`wos:log:{instance_id}`)."""

from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aioredis


class RedisAsyncLogHandler(logging.Handler):
    """Non-blocking: schedules LPUSH + LTRIM on the running asyncio loop."""

    def __init__(self, redis_client: aioredis.Redis, instance_id: str) -> None:  # type: ignore[type-arg]
        super().__init__()
        self._redis = redis_client
        self._key = f"wos:log:{instance_id}"

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            loop = asyncio.get_running_loop()
            loop.create_task(self._push(msg))
        except RuntimeError:
            pass

    async def _push(self, msg: str) -> None:
        await self._redis.lpush(self._key, msg)
        await self._redis.ltrim(self._key, 0, 199)
