"""Generalized distributed lease — the gift-code ``SET NX EX`` pattern, reusable.

Single-owner, token-fenced, TTL self-heals a crashed holder. Same mechanics as
``scheduler.claims.CooperativeClaims`` / the gift-code redeem lock, but named and
with a ``refresh`` so a long holder can extend without a release/re-acquire race.
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

from . import keys

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# Atomic compare-and-delete: only release if we still own the lease.
_RELEASE_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
else
    return 0
end
"""

# Atomic compare-and-extend: only refresh the TTL if we still own the lease.
_REFRESH_LUA = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('SET', KEYS[1], ARGV[1], 'XX', 'EX', ARGV[2])
else
    return false
end
"""


class Lease:
    """A named, token-fenced lease over a Redis key."""

    def __init__(self, name: str, *, redis: aioredis.Redis) -> None:
        self._key = keys.lease_key(name)
        self._redis = redis
        self._release_script = redis.register_script(_RELEASE_LUA)
        self._refresh_script = redis.register_script(_REFRESH_LUA)

    async def acquire(self, *, ttl_s: int, token: str | None = None) -> str | None:
        """Try to take the lease. Returns the owner token on success, else None."""
        tok = token or uuid.uuid4().hex
        ok = await self._redis.set(self._key, tok, nx=True, ex=int(ttl_s))
        return tok if ok else None

    async def refresh(self, token: str, *, ttl_s: int) -> bool:
        """Extend the TTL iff we still own it."""
        rv = await self._refresh_script(keys=[self._key], args=[token, int(ttl_s)])
        return bool(rv)

    async def release(self, token: str) -> bool:
        """Release iff we still own it (a late finisher can't free a successor)."""
        rv = await self._release_script(keys=[self._key], args=[token])
        try:
            return int(rv) == 1
        except (TypeError, ValueError):
            return False


@contextlib.asynccontextmanager
async def lease(
    name: str,
    *,
    ttl_s: int,
    redis: aioredis.Redis,
    token: str | None = None,
) -> AsyncIterator[str | None]:
    """``async with lease(...) as token:`` — token is None if the lease was held.

    Releases in ``finally`` only if we actually acquired it.
    """
    leaser = Lease(name, redis=redis)
    tok = await leaser.acquire(ttl_s=ttl_s, token=token)
    try:
        yield tok
    finally:
        if tok is not None:
            with contextlib.suppress(Exception):
                await leaser.release(tok)
