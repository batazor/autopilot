"""Shared FastAPI dependencies."""
from __future__ import annotations

import redis
from redis.backoff import ExponentialBackoff
from redis.retry import Retry

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
# Pooled connections that sit idle can have their socket dropped underneath
# them (server idle timeout, NAT/keepalive expiry, a network blip). Without a
# health check redis-py hands the dead connection back out and the next command
# fails with ``ConnectionError: ... Bad file descriptor`` / ``Connection reset``
# instead of reconnecting. PING any connection idle longer than this before use
# so a stale socket is detected and replaced transparently.
_REDIS_HEALTH_CHECK_INTERVAL_S = 30
# Retry the *whole command* on connection/timeout errors: the failed connection
# is disconnected and the retry runs on a fresh one, so a single bad socket is
# recovered rather than bubbling a 500 to the dashboard.
_REDIS_RETRIES = 3


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = load_settings()
        kwargs = {
            "decode_responses": True,
            "max_connections": _REDIS_MAX_CONNECTIONS,
            "timeout": _REDIS_POOL_WAIT_TIMEOUT_S,
            "socket_connect_timeout": _REDIS_SOCKET_CONNECT_TIMEOUT_S,
            "socket_timeout": _REDIS_SOCKET_TIMEOUT_S,
            "health_check_interval": _REDIS_HEALTH_CHECK_INTERVAL_S,
            "retry": Retry(ExponentialBackoff(cap=0.5, base=0.05), _REDIS_RETRIES),
            "retry_on_error": [redis.ConnectionError, redis.TimeoutError],
        }
        # ``socket_keepalive`` is a TCP-only option. Over a unix socket (the prod
        # deployment uses ``unix:///var/run/redis/redis.sock``) redis-py forwards
        # it to ``AbstractConnection.__init__``, which rejects it with TypeError
        # — the pool builds lazily so it looks fine, but the first command (and
        # the /health ping) blows up and reports Redis as "unreachable".
        if not settings.redis.url.startswith("unix://"):
            kwargs["socket_keepalive"] = True
        pool = redis.BlockingConnectionPool.from_url(settings.redis.url, **kwargs)
        _redis_client = redis.Redis(connection_pool=pool)
        instrument_redis_client(_redis_client, component="api")
    return _redis_client
