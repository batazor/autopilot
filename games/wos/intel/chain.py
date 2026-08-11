"""Multi-march chaining: re-enqueue ``intel_run`` while slots + stamina allow.

One ``intel_run`` pass dispatches exactly ONE march. Accounts with several
march queues («Марш 1/2» and up) should spend every free slot in the same
board window, so both dispatch branches end with the ``queue_next_intel_run``
exec: it re-enqueues ``intel_run`` when

* the march-slot view (``marches.capacity`` fact, else the ``capacity``
  arg default) minus in-flight occupancy (``marches.active_count`` fact +
  active march-lease reservations) leaves a free slot, and
* the last on-board stamina read covers another event (``cost``).

A chained run that finds nothing to start simply completes — the planner
skips, the optional waits tolerate the missing screens — and does NOT reach
this exec again, so the chain is bounded by capacity, stamina and fresh
markers rather than a counter.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from games.wos.core.resources import adapter as resource_adapter
from games.wos.intel.board_cache import board_exhausted
from games.wos.intel.planner import DEFAULT_COST_PER_EVENT
from games.wos.intel.state import as_float_arg, as_int_arg, decode_redis_text

if TYPE_CHECKING:
    from tasks.dsl_exec.context import DslExecContext

logger = logging.getLogger(__name__)

# Conservative baseline when the ``marches.capacity`` fact is unread: the
# second march queue unlocks very early, so 2 holds for developed accounts
# (bs5 live shows «Марш 1/2»). Accounts still on one queue lose nothing — the
# chained run's deploy simply fails and the popup handler cleans up.
DEFAULT_MARCH_CAPACITY = 2

_INTEL_SCENARIO = "intel_run"
_DEFAULT_PRIORITY = 80_060  # mirrors intel_run's own priority
# Short gap between chained passes so a full board's free marches go out back-to-
# back (the fight no longer blocks ~90s watching the battle). Just enough for the
# deploy/back-out to settle before the next run re-opens the board.
_DEFAULT_DELAY_S = 1.5


def _to_int(raw: Any, default: int = 0) -> int:
    try:
        return int(float(decode_redis_text(raw) or default))
    except (TypeError, ValueError):
        return default


async def free_march_slots(
    redis: Any,
    player_id: str,
    *,
    now: float,
    capacity_default: int = DEFAULT_MARCH_CAPACITY,
) -> tuple[int, dict[str, Any]]:
    """(free slots, detail) from player facts + the active reservation ledger."""
    raw = await redis.hgetall(f"wos:player:{player_id}:state")
    state = {decode_redis_text(k): decode_redis_text(v) for k, v in (raw or {}).items()}
    capacity = _to_int(state.get("marches.capacity"), capacity_default)
    occupied = _to_int(state.get("marches.active_count"))
    ledger = await resource_adapter.read_ledger(redis, player_id, now)
    held = sum(_to_int(entry.get("slots")) for entry in ledger)
    free = max(0, capacity - occupied - held)
    return free, {
        "capacity": capacity,
        "occupied": occupied,
        "held_slots": held,
        "stamina": state.get("stamina"),
    }


async def maybe_chain_after_tap(
    ctx: DslExecContext, *, board_left: int, stamina: float | None, cost: int
) -> bool:
    """Re-enqueue ``intel_run`` the instant a pin is tapped — the robust chain.

    Called from ``tap_intel_fight`` right after a successful tap, BEFORE the
    fight/deploy flow can be preempted by a device-level popup or time out on a
    result screen (both silently killed the old end-of-run ``queue_next``, so the
    board stalled with pins left and only the ~15-min coordinator tick could
    resume it). Enqueuing here makes the follow-up survive whatever happens to
    the current run's battle.

    Bounded so it terminates: only a run that actually TAPPED chains, and only
    while the board still shows viable pins (``board_left``) and the stamina
    budget covers another event. The first run that can't tap (no dispatchable
    pin — e.g. every remaining pin needs a march slot and all are busy) does not
    chain, ending the sweep until a slot frees (the coordinator picks it up).
    """
    if ctx.redis_client is None or not ctx.player_id:
        return False
    if board_left <= 0:
        return False
    if stamina is not None and stamina < cost:
        return False
    # Best-effort: a failed chain enqueue must never fail the tap that already
    # dispatched the pin — the coordinator's short cooldown is the fallback.
    try:
        from tasks.dsl_scenario_helpers import enqueue_scenario

        return await enqueue_scenario(
            redis_async=ctx.redis_client,
            instance_id=ctx.instance_id,
            player_id=ctx.player_id,
            scenario=_INTEL_SCENARIO,
            priority=_DEFAULT_PRIORITY,
            run_at=time.time() + _DEFAULT_DELAY_S,
            skip_if_duplicate=False,
        )
    except Exception:
        logger.debug("intel: chain-after-tap enqueue failed", exc_info=True)
        return False


_CLAIM_SCENARIO = "intel_claim_reward"
_CLAIM_DELAY_S = 40.0


async def schedule_reward_claim(ctx: DslExecContext, *, delay: float = _CLAIM_DELAY_S) -> bool:
    """Push a delayed board-reward pickup after a pin is dispatched.

    An intel fight sends a march that travels, fights and RETURNS with the
    reward; the green «Получить все» pill only appears once the march is back —
    by which time the sweep has cleared the board and the bot has gone home, so
    the reward sat until the twice-a-day claim cron (user-reported, bs3
    2026-08-09). Enqueue ``intel_claim_reward`` ~``delay`` s out (≈ march return)
    to come back and press it. ``skip_if_duplicate`` keeps a single pickup
    pending across a multi-pin sweep instead of one per tap.
    """
    if ctx.redis_client is None or not ctx.player_id:
        return False
    try:
        from tasks.dsl_scenario_helpers import enqueue_scenario

        return await enqueue_scenario(
            redis_async=ctx.redis_client,
            instance_id=ctx.instance_id,
            player_id=ctx.player_id,
            scenario=_CLAIM_SCENARIO,
            priority=55_000,
            run_at=time.time() + max(0.0, delay),
            skip_if_duplicate=True,
        )
    except Exception:
        logger.debug("intel: reward-claim schedule failed", exc_info=True)
        return False


async def queue_next_intel_run(ctx: DslExecContext) -> None:
    """Re-enqueue ``intel_run`` if a march slot and the stamina budget remain.

    Args:
      capacity: fallback march-queue count when ``marches.capacity`` is unread
        (default 2).
      cost: stamina one more event needs, default 10 (mirrors the planner).
      priority: queue priority for the chained run, default 80_060.
      delay: seconds before the chained run may start, default 5 (lets the
        deploy/battle screens settle before the next pass navigates back).
    """
    if ctx.redis_client is None or not ctx.player_id:
        ctx.result.update({"action": "skipped", "reason": "no_redis_or_player"})
        return

    now = time.time()
    free, detail = await free_march_slots(
        ctx.redis_client,
        ctx.player_id,
        now=now,
        capacity_default=as_int_arg(ctx.args, "capacity", DEFAULT_MARCH_CAPACITY),
    )
    cost = as_float_arg(ctx.args, "cost", float(DEFAULT_COST_PER_EVENT))
    stamina = None
    try:
        stamina = float(decode_redis_text(detail.get("stamina")) or "")
    except (TypeError, ValueError):
        stamina = None

    # Zero free slots does NOT end the chain: camp pins dispatch without a
    # march-queue slot (operator-confirmed), so the next pass can still harvest
    # them — its tap-gate restricts the pick to deployless kinds and a pass
    # that finds none simply completes without re-reaching this exec, keeping
    # the chain bounded by fresh camps + stamina.
    camp_only = free < 1
    # Unknown stamina (read failed) → chain anyway: the next run re-reads it on
    # the board and its planner makes the real call.
    if stamina is not None and stamina < cost:
        ctx.result.update(
            {"action": "skipped", "reason": "insufficient_stamina", **detail}
        )
        return

    # Board memory: the pass that just ran recorded how many actionable pins
    # remain. Zero → end the chain HERE instead of paying one more full
    # navigate + claim + detect round trip just to discover the empty board.
    if await board_exhausted(ctx.redis_client, ctx.player_id):
        ctx.result.update({"action": "skipped", "reason": "board_exhausted", **detail})
        return

    from tasks.dsl_scenario_helpers import enqueue_scenario

    delay = as_float_arg(ctx.args, "delay", _DEFAULT_DELAY_S)
    ok = await enqueue_scenario(
        redis_async=ctx.redis_client,
        instance_id=ctx.instance_id,
        player_id=ctx.player_id,
        scenario=_INTEL_SCENARIO,
        priority=as_int_arg(ctx.args, "priority", _DEFAULT_PRIORITY),
        run_at=now + max(0.0, delay),
        # The current intel_run is still the running task — dedup against the
        # queue would eat the follow-up, so push unconditionally. The chain is
        # bounded: a run that dispatches nothing never reaches this exec.
        skip_if_duplicate=False,
    )
    ctx.result.update(
        {
            "action": "queued" if ok else "enqueue_failed",
            "scenario": _INTEL_SCENARIO,
            "free_slots": free,
            **({"chain_window": "camp_only"} if camp_only else {}),
            **detail,
        }
    )
    logger.info(
        "dsl exec queue_next_intel_run: action=%s player=%s free=%d detail=%s",
        ctx.result.get("action"),
        ctx.player_id,
        free,
        detail,
    )
