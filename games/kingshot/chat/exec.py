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


DSL_EXEC_HANDLERS = {"alliance_broadcast_tick": _exec_alliance_broadcast_tick}
