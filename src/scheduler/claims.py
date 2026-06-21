from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "wos:claimed"

# Atomic compare-and-delete: only release the lock if we still own it.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""


class CooperativeClaims:
    """Redis-based cooperative task locks using SET NX with TTL."""

    def __init__(self, redis_client: aioredis.Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    def _key(self, task_type: str) -> str:
        return f"{_KEY_PREFIX}:{task_type}"

    async def claim(self, task_type: str, player_id: str, ttl: int) -> bool:
        key = self._key(task_type)
        result = await self._redis.set(key, player_id, nx=True, ex=ttl)
        if result:
            logger.debug("Claimed cooperative task %s for %s", task_type, player_id)
        return bool(result)

    async def release(self, task_type: str, player_id: str) -> None:
        key = self._key(task_type)
        released = await self._redis.eval(_RELEASE_LUA, 1, key, player_id)
        if released:
            logger.debug("Released cooperative task %s by %s", task_type, player_id)

    async def is_claimed(self, task_type: str) -> bool:
        key = self._key(task_type)
        return bool(await self._redis.exists(key))
