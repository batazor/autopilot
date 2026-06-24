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


DSL_EXEC_HANDLERS = {"alliance_broadcast_tick": _exec_alliance_broadcast_tick}
