"""Shared FastAPI dependencies."""
from __future__ import annotations

import redis

from config.loader import load_settings
from config.redis_metrics import instrument_redis_client

_redis_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        settings = load_settings()
        _redis_client = redis.Redis.from_url(settings.redis.url, decode_responses=True)
        instrument_redis_client(_redis_client, component="api")
    return _redis_client
