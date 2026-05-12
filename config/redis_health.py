"""Redis reachability checks — fail fast with a clear operator message."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

import redis
import redis.asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

_DEFAULT_SOCKET_CONNECT_TIMEOUT_S = 5.0


def format_redis_unreachable_message(url: str, exc: BaseException) -> str:
    """Human-readable multi-line message for logs / SystemExit."""
    return (
        "Cannot connect to Redis.\n\n"
        f"  URL: {url}\n"
        f"  Error: {exc}\n\n"
        "Start Redis (from the repo root: `docker compose up -d redis`) "
        "or change `redis.url` in config/settings.yaml."
    )


async def ping_async_redis_or_exit(
    client: aioredis.Redis,
    *,
    url: str,
    connect_timeout_s: float = _DEFAULT_SOCKET_CONNECT_TIMEOUT_S,
) -> None:
    """Ping the async client; on failure close it and raise ``SystemExit`` with guidance."""
    try:
        await asyncio.wait_for(client.ping(), timeout=connect_timeout_s + 2.0)
    except (RedisError, OSError, TimeoutError) as exc:
        msg = format_redis_unreachable_message(url, exc)
        logger.critical("%s", " | ".join(msg.splitlines()))
        try:
            await client.aclose()
        except Exception:
            logger.debug("Redis aclose after failed ping", exc_info=True)
        raise SystemExit(msg) from exc


def sync_redis_from_url_or_exit(
    url: str,
    *,
    decode_responses: bool = True,
    socket_connect_timeout: float = _DEFAULT_SOCKET_CONNECT_TIMEOUT_S,
) -> redis.Redis:
    """Open a sync Redis client and ``PING``; on failure exit the process with guidance."""
    client: Any = redis.Redis.from_url(
        url,
        decode_responses=decode_responses,
        socket_connect_timeout=socket_connect_timeout,
    )
    try:
        client.ping()
    except (RedisError, OSError) as exc:
        msg = format_redis_unreachable_message(url, exc)
        logger.critical("%s", " | ".join(msg.splitlines()))
        with contextlib.suppress(Exception):
            client.close(close_connection_pool=True)
        raise SystemExit(msg) from exc
    return client


def verify_sync_redis_url(url: str) -> None:
    """One-off connectivity check (open, ping, close). Raises ``SystemExit`` on failure."""
    client: Any = redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=_DEFAULT_SOCKET_CONNECT_TIMEOUT_S,
    )
    try:
        client.ping()
    except (RedisError, OSError) as exc:
        msg = format_redis_unreachable_message(url, exc)
        logger.critical("%s", " | ".join(msg.splitlines()))
        raise SystemExit(msg) from exc
    finally:
        with contextlib.suppress(Exception):
            client.close(close_connection_pool=True)
