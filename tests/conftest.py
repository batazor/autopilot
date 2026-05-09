from __future__ import annotations

import os
from typing import AsyncIterator, Iterator

import pytest
import redis
import redis.asyncio as aioredis
from testcontainers.redis import RedisContainer


def _redis_url_from_container(c: RedisContainer) -> str:
    host = c.get_container_host_ip()
    port = int(c.get_exposed_port(6379))
    return f"redis://{host}:{port}/0"


@pytest.fixture(scope="session")
def redis_container() -> Iterator[RedisContainer]:
    """Session-scoped Redis container for integration tests.

    Skips tests marked `integration` when Docker isn't available.
    """
    # Allow disabling in CI/local when Docker is not present.
    if os.environ.get("WOS_TESTCONTAINERS", "").strip() in {"0", "false", "no"}:
        pytest.skip("Testcontainers disabled via WOS_TESTCONTAINERS=0")

    c = RedisContainer("redis:7-alpine")
    try:
        c.start()
    except Exception as e:
        pytest.skip(f"Testcontainers Redis unavailable (Docker?): {e!s}")
    try:
        yield c
    finally:
        try:
            c.stop()
        except Exception:
            pass


@pytest.fixture()
async def redis_async(redis_container: RedisContainer) -> AsyncIterator[aioredis.Redis]:
    """Async redis client flushed per test."""
    url = _redis_url_from_container(redis_container)
    r = aioredis.from_url(url, decode_responses=True)
    try:
        await r.flushdb()
        yield r
    finally:
        await r.aclose()


@pytest.fixture()
def redis_sync(redis_container: RedisContainer) -> Iterator[redis.Redis]:
    """Sync redis client flushed per test (for code using redis.Redis, not asyncio)."""
    url = _redis_url_from_container(redis_container)
    r = redis.Redis.from_url(url, decode_responses=True)
    r.flushdb()
    try:
        yield r
    finally:
        try:
            r.close()
        except Exception:
            pass

