"""Shared FastAPI dependencies."""
from __future__ import annotations

import redis

from config.loader import load_settings
from config.redis_metrics import instrument_redis_client

_redis_client: redis.Redis | None = None

# Bound the per-process sync pool so a runaway endpoint can't open hundreds of
# Redis sockets before failing. ``BlockingConnectionPool`` waits up to
# ``timeout`` seconds for an available connection instead of raising
# ``ConnectionError`` immediately under contention.
_REDIS_MAX_CONNECTIONS = 32
_REDIS_POOL_WAIT_TIMEOUT_S = 5.0
_REDIS_SOCKET_CONNECT_TIMEOUT_S = 5.0
_REDIS_SOCKET_TIMEOUT_S = 10.0


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = load_settings()
        pool = redis.BlockingConnectionPool.from_url(
            settings.redis.url,
            decode_responses=True,
            max_connections=_REDIS_MAX_CONNECTIONS,
            timeout=_REDIS_POOL_WAIT_TIMEOUT_S,
            socket_connect_timeout=_REDIS_SOCKET_CONNECT_TIMEOUT_S,
            socket_timeout=_REDIS_SOCKET_TIMEOUT_S,
        )
        _redis_client = redis.Redis(connection_pool=pool)
        instrument_redis_client(_redis_client, component="api")
    return _redis_client
