from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Iterator

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
        with contextlib.suppress(Exception):
            c.stop()


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
        with contextlib.suppress(Exception):
            r.close()


@pytest.fixture()
def pin_click_to_center(monkeypatch: pytest.MonkeyPatch) -> None:
    """Disable random-in-bbox click jitter so tests can pin exact pixel coords.

    Production code samples a random point inside the region/template bbox; that
    is intentional (varies per-click to look human-like). Tests that pre-date the
    randomisation pin the bbox centre, so they opt into this fixture instead of
    encoding tolerance ranges.
    """
    from layout import bbox_percent as _bp
    from navigation import navigator as _nav
    from tasks import dsl_scenario_inline_mixin as _inline

    monkeypatch.setattr(
        _inline,
        "bbox_percent_random_point_to_device_point",
        _bp.bbox_percent_center_to_device_point,
    )
    monkeypatch.setattr(
        _nav,
        "bbox_percent_random_point_to_device_point",
        _bp.bbox_percent_center_to_device_point,
    )

