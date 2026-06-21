"""Shared context + helpers for DSL ``exec:`` handlers (see :mod:`tasks.dsl_exec.registry`)."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DslExecHandler = Callable[["DslExecContext"], Awaitable[None]]


@dataclass(frozen=True)
class DslExecContext:
    redis_client: Any | None
    """Async Redis client (same as ``DslScenarioTask.redis_client``)."""

    player_id: str
    """Queue / config player id (Redis hash ``wos:player:<player_id>:state``)."""

    instance_id: str
    """ADB instance id (device)."""

    args: dict[str, Any] = field(default_factory=dict)
    """Sibling YAML keys on the ``exec:`` step (everything except ``exec`` /
    ``cond``). Each handler reads what it needs; unknown keys are silently
    ignored so adding a new arg never breaks older handlers."""

    result: dict[str, Any] = field(default_factory=dict)
    """Best-effort diagnostics the handler can expose on the scenario trace."""


def _decode_redis_raw(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        try:
            return raw.decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    return str(raw).strip()


async def _resolve_player_id_for_device_level_exec(ctx: DslExecContext) -> str:
    """Resolve a player binding for execs called from ``device_level: true``
    scenarios.

    Device-level scenarios (``who_i_am``, ``building.upgrade`` during
    tutorial, popup dismissers) are queued with ``player_id=""``. Some of
    their exec handlers still want to write into a specific player's state
    once ``who_i_am`` has run and ``active_player`` is set on the instance
    hash — this helper resolves that binding.

    Player-bound scenarios MUST NOT use this — the implicit identity gate in
    ``DslScenarioExecuteMixin.execute`` already guarantees ``ctx.player_id``
    is non-empty there, and reading the helper buys nothing but a stale
    fallback path.
    """
    player_id = (ctx.player_id or "").strip()
    if player_id or ctx.redis_client is None:
        return player_id
    try:
        raw = await ctx.redis_client.hget(
            f"wos:instance:{ctx.instance_id}:state",
            "active_player",
        )
    except Exception:
        logger.debug("dsl exec: active_player lookup failed", exc_info=True)
        return ""
    return _decode_redis_raw(raw)
