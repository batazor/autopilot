"""DSL exec handler for the Kingshot alliance-broadcast tick.

Thin wrapper over the shared core (:mod:`modules.broadcast.runner`), bound to the
Kingshot game. Inert until the chat screen graph + input/send regions are labeled
on a live device — see this module's README.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

_GAME = "kingshot"


async def _exec_alliance_broadcast_tick(ctx: DslExecContext) -> None:
    from modules.broadcast.runner import run_broadcast_tick

    await run_broadcast_tick(ctx, game=_GAME)


async def _exec_alliance_broadcast_send_one(ctx: DslExecContext) -> None:
    """Post one specific message now (operator "Send now"). Message id arrives via
    a per-instance Redis key (set by the API); falls back to a step arg."""
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
