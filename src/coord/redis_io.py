"""Shared Redis helpers for the coord IO wrappers.

The wrappers (:mod:`coord.fleet`, :mod:`coord.bus`, :mod:`coord.lease`,
:mod:`coord.barrier`) take an *injected* async Redis client — same as
``RedisQueue`` — so they work from both the worker (its own client) and the
scheduler (``services.get_scheduler_async_redis``). This module only holds the
decode helpers they share; clients with and without ``decode_responses`` both
flow through here.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping


def decode_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return str(value)


def decode_hash(raw: Mapping[Any, Any] | None) -> dict[str, str]:
    if not raw:
        return {}
    return {decode_str(k): decode_str(v) for k, v in raw.items()}


async def default_async_redis() -> Any:
    """The scheduler's shared async client — a convenience default for callers
    that don't already hold one (the worker injects its own)."""
    from services import get_scheduler_async_redis

    return await get_scheduler_async_redis()
