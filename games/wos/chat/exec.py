"""DSL exec handler for the WoS alliance-broadcast tick.

Thin wrapper: all logic lives in the game-agnostic core
(:mod:`modules.broadcast.runner`); this only binds the game. Driven by the
``broadcast_tick`` cron scenario — a cheap no-op unless a message is due and this
account is the elected broadcaster for its alliance.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

_GAME = "wos"


async def _exec_alliance_broadcast_tick(ctx: DslExecContext) -> None:
    from modules.broadcast.runner import run_broadcast_tick

    await run_broadcast_tick(ctx, game=_GAME)


async def _exec_alliance_broadcast_send_one(ctx: DslExecContext) -> None:
    """Post one specific message now (operator "Send now"). The API hands the
    message id over via a per-instance Redis key; fall back to a step arg."""
    from modules.broadcast import keys
    from modules.broadcast.runner import send_one

    mid = ""
    if ctx.redis_client is not None:
        try:
            raw = await ctx.redis_client.get(keys.send_now_key(ctx.instance_id))
            mid = (raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")).strip()
        except Exception:
            mid = ""
    mid = mid or str((ctx.args or {}).get("message_id") or "").strip()
    await send_one(ctx, game=_GAME, message_id=mid)


DSL_EXEC_HANDLERS = {
    "alliance_broadcast_tick": _exec_alliance_broadcast_tick,
    "alliance_broadcast_send_one": _exec_alliance_broadcast_send_one,
}
