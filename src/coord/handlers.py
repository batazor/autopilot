"""Directive handler registry — engine-safe handlers only.

A handler NEVER taps the device directly: anything that should tap is *enqueued
as a scenario* via the existing ``RedisQueue.schedule`` (with
``skip_if_duplicate``), so it flows through the worker's single
approval-gated/screen-gated executor — click-approval mode is respected by
construction. Game-specific scenario keys arrive in ``directive.payload`` (the
orchestrator is game-aware), keeping this module game-agnostic.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from . import keys

if TYPE_CHECKING:
    from .bus import DirectiveBus
    from .models import Directive

logger = logging.getLogger(__name__)

# Queue priority bands (match scheduler/stamina conventions: ordinary tasks at
# 80k). A device-level identity switch is urgent — lift it above ordinary work.
DEFAULT_PRIORITY = 80_000
SWITCH_PRIORITY = 90_000


@dataclass(frozen=True, slots=True)
class HandlerContext:
    redis: Any
    bus: DirectiveBus
    queue: Any           # scheduler.queue.RedisQueue | None
    instance_id: str
    active_player: str = ""


async def _ping(ctx: HandlerContext, directive: Directive) -> str:
    return "pong"


async def _noop(ctx: HandlerContext, directive: Directive) -> str:
    return "noop"


async def _enqueue_scenario(ctx: HandlerContext, directive: Directive) -> str:
    if ctx.queue is None:
        return "no_queue"
    scenario = str(directive.payload.get("scenario") or "").strip()
    if not scenario:
        return "no_scenario"
    player_id = str(directive.payload.get("player_id") or "")
    raw_args = directive.payload.get("args")
    args = dict(raw_args) if isinstance(raw_args, dict) else None
    try:
        priority = int(directive.payload.get("priority") or DEFAULT_PRIORITY)
    except (TypeError, ValueError):
        priority = DEFAULT_PRIORITY
    await ctx.queue.schedule(
        task_id=f"coord:{directive.directive_id}",
        player_id=player_id,
        task_type=scenario,
        priority=priority,
        run_at=time.time(),
        instance_id=ctx.instance_id,
        dsl_scenario=scenario,
        args=args,
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
    return "queued"


async def _request_account_switch(ctx: HandlerContext, directive: Directive) -> str:
    """Record the desired active player + enqueue the (game-provided) switch
    scenario. The actual switch is a scenario — gated by approval — never a tap
    from here. (The legacy ``_switcher`` UI path is dead code; this avoids it.)"""
    fid = str(directive.payload.get("player_id") or "").strip()
    if not fid:
        return "no_player"
    try:
        await ctx.redis.hset(
            keys.instance_state_key(ctx.instance_id), "active_player_switch_request", fid
        )
    except Exception:
        logger.debug("coord: failed to record switch request", exc_info=True)
    scenario = str(directive.payload.get("scenario") or "").strip()
    if scenario and ctx.queue is not None:
        await ctx.queue.schedule(
            task_id=f"coord-switch:{directive.directive_id}",
            player_id="",  # device-level: switching the logged-in identity
            task_type=scenario,
            priority=SWITCH_PRIORITY,
            run_at=time.time(),
            instance_id=ctx.instance_id,
            dsl_scenario=scenario,
            args={"target_player": fid},
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )
        return "switch_enqueued"
    return "switch_requested"


async def _barrier_signal(ctx: HandlerContext, directive: Directive) -> str:
    bid = str(directive.payload.get("barrier_id") or "").strip()
    if not bid:
        return "no_barrier"
    from .barrier import Barrier

    party = str(directive.payload.get("party") or ctx.instance_id)
    note = str(directive.payload.get("note") or "")
    status = await Barrier(bid, redis=ctx.redis).arrive(party, now=time.time(), note=note)
    return f"barrier:{status}"


REGISTRY: dict[str, Any] = {
    "ping": _ping,
    "noop": _noop,
    "enqueue_scenario": _enqueue_scenario,
    "request_account_switch": _request_account_switch,
    "barrier_signal": _barrier_signal,
}


def get(kind: str) -> Any:
    return REGISTRY.get(str(kind or ""))
