"""Redis-backed adapter: bridge the pure allocator to live state + the queue.

Split so the decision logic stays unit-testable:

* :func:`plan` is pure — it takes a plain ``state`` dict (a decoded
  ``wos:player:<pid>:state`` hash) and ``now``, and returns the allocator
  :class:`~allocator.Decision` plus the computed stamina estimate. No IO.
* :func:`write_decision_trace` / :func:`enqueue_decision` are the thin async
  side-effects (ring-buffer for the UI, queue push for the worker).

The scheduler calls these per active player each tick. The orchestration
(running-task guard, enabled flag) lives in the scheduler where those helpers
already exist; see ``scheduler/runner.py:_run_stamina_planner``.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .allocator import (
    CONSUME,
    IDLE,
    SUPPLY,
    Decision,
    DemandRuntime,
    SupplyRuntime,
    Verdict,
    allocate,
)
from .model import (
    DEFAULT_BUDGET_PATH,
    Budget,
    is_active,
    is_triggered,
    quota_field,
    quota_period,
    seconds_to_afford,
    seconds_to_cap,
)
from .model import estimate_stamina as _estimate_stamina

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from scheduler.queue import RedisQueue

logger = logging.getLogger(__name__)

DEFAULT_PRIORITY = 80_000
TRACE_RETENTION_SECONDS = 24 * 3600
TRACE_RETENTION_CAP = 50

# String spellings of booleans that Redis state may hold. eval_cond only coerces
# *numeric* strings, so "false" would otherwise read as a truthy non-empty
# string — normalise these to 1/0 before evaluating event-window conditions.
_TRUE_TOKENS = {"true", "yes", "on"}
_FALSE_TOKENS = {"false", "no", "off"}

_BUDGET_CACHE: dict[str, tuple[float, Budget]] = {}


def load_budget(path: str | Path | None = None) -> Budget:
    """``Budget.load()`` cached by file mtime — avoids re-reading budget.yaml
    from disk on every scheduler tick, while still picking up edits (mtime
    changes invalidate the cache)."""
    p = Path(path) if path else DEFAULT_BUDGET_PATH
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _BUDGET_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    budget = Budget.load(p)
    _BUDGET_CACHE[key] = (mtime, budget)
    return budget


def decision_signature(decision: Decision) -> str:
    """Identity of a decision, ignoring the moving estimate — lets the planner
    skip rewriting the trace when its decision is unchanged tick-to-tick."""
    return f"{decision.action}|{decision.target_id}|{decision.reason}"


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Outcome of one planning pass for a player."""

    est: float | None       # interpolated stamina estimate (None → never read)
    period: str             # quota game-day key this decision counted against
    decision: Decision


def _to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int:
    f = _to_float(value)
    return int(f) if f is not None else 0


def _eval_context(state: dict[str, Any]) -> dict[str, Any]:
    """Flat context for ``active_when`` / ``trigger_when``, with boolean-ish
    string flags normalised so ``"false"`` is falsy under eval_cond."""
    ctx: dict[str, Any] = {}
    for k, v in state.items():
        if isinstance(v, str) and v.strip().lower() in _TRUE_TOKENS:
            ctx[k] = 1
        elif isinstance(v, str) and v.strip().lower() in _FALSE_TOKENS:
            ctx[k] = 0
        else:
            ctx[k] = v
    return ctx


def plan(budget: Budget, state: dict[str, Any], now: float) -> PlanResult:
    """Resolve live ``state`` into runtime snapshots and run the allocator.

    Pure: ``state`` is a decoded Redis hash (string values), ``now`` a unix ts.
    Reads ``stamina`` / ``stamina_read_at`` for the estimate, ``quota:<day>:<id>``
    counters for usage, and any event-window flags referenced by conditions.
    """
    last = _to_float(state.get("stamina"))
    # The OCR step auto-writes `<field>_at` when it stores `stamina`, so the
    # read timestamp lives at `stamina_at` (fall back to a legacy/explicit
    # `stamina_read_at`, then to `now` for a just-stored value).
    read_at = _to_float(state.get("stamina_at"))
    if read_at is None:
        read_at = _to_float(state.get("stamina_read_at"))
    if read_at is None:
        read_at = now
    est = _estimate_stamina(
        last, read_at, now, cap=budget.cap, regen_per_hour=budget.regen_per_hour
    )
    period = quota_period(now, budget.daily_reset_utc)

    # No OCR reading yet → don't act blind. The planner stays idle until the
    # reader (overlay-triggered) populates `stamina`; acting on a None estimate
    # would treat it as 0 and fire spurious refills.
    if est is None:
        return PlanResult(
            est=None,
            period=period,
            decision=Decision(action=IDLE, reason="no_stamina_reading"),
        )

    ctx = _eval_context(state)

    demand_rts = [
        DemandRuntime(
            demand=d,
            active=is_active(d, ctx),
            quota_used=_to_int(state.get(quota_field(period, d.id))),
        )
        for d in budget.demands
    ]

    # Computed fields a supply's trigger_when may reference: the top active
    # demand still holding quota (the one a refill would unblock).
    s_est = 0.0 if est is None else est
    candidates = [r for r in demand_rts if r.active and r.has_quota]
    top = max(candidates, key=lambda r: r.demand.priority, default=None)
    supply_ctx = {
        **ctx,
        "stamina": s_est,
        "top_demand_priority": top.demand.priority if top else 0,
        "top_demand_cost": top.demand.cost if top else 0,
    }
    supply_rts = [
        SupplyRuntime(
            supply=s,
            triggered=is_triggered(s, supply_ctx),
            quota_used=_to_int(state.get(quota_field(period, s.id))),
        )
        for s in budget.supplies
    ]

    decision = allocate(
        est,
        demand_rts,
        cap=budget.cap,
        regen_per_hour=budget.regen_per_hour,
        supplies=supply_rts,
        hours_to_next_regen=budget.overflow_horizon_hours,
    )
    return PlanResult(est=est, period=period, decision=decision)


def build_view(budget: Budget, state: dict[str, Any], now: float) -> dict[str, Any]:
    """Live UI snapshot for one player: estimate, cap math, and per-demand rows.

    Pure: reuses :func:`plan` for the current decision (and its per-demand
    verdicts) so the dashboard never re-implements allocator logic. The recent
    decision history is read from Redis by the API layer and attached there.
    """
    result = plan(budget, state, now)
    s_to_cap = seconds_to_cap(
        result.est, cap=budget.cap, regen_per_hour=budget.regen_per_hour
    )
    verdict_by_id = {v.demand_id: v for v in result.decision.verdicts}
    ctx = _eval_context(state)

    demands = []
    afford_times: list[float] = []
    for d in budget.demands:
        v = verdict_by_id.get(d.id)
        used = _to_int(state.get(quota_field(result.period, d.id)))
        active = is_active(d, ctx)
        has_quota = d.daily_quota is None or used < d.daily_quota
        if active and has_quota:
            afford_times.append(
                seconds_to_afford(result.est, d.cost, regen_per_hour=budget.regen_per_hour)
            )
        demands.append({
            "id": d.id,
            "task_type": d.task_type,
            "priority": d.priority,
            "cost": d.cost,
            "daily_quota": d.daily_quota,
            "quota_used": used,
            "reserve_floor": d.reserve_floor,
            "active": active,
            "verdict": v.reason if v else None,
            "selected": bool(v and v.selected),
        })

    # Time until the next eligible demand becomes affordable — a natural TTL for
    # backing off instead of re-polling. None when acting now or nothing pending.
    retry_after: float | None = None
    if result.decision.action != CONSUME and afford_times:
        soonest = min(afford_times)
        retry_after = None if math.isinf(soonest) else soonest

    return {
        "enabled": budget.enabled,
        "cap": budget.cap,
        "regen_per_hour": budget.regen_per_hour,
        "est": result.est,
        "stamina_read_at": _to_float(state.get("stamina_at"))
        or _to_float(state.get("stamina_read_at")),
        "seconds_to_cap": None if math.isinf(s_to_cap) else s_to_cap,
        "retry_after_s": retry_after,
        "period": result.period,
        "action": result.decision.action,
        "reason": result.decision.reason,
        "target": result.decision.target_id,
        "overflow_pressure": result.decision.overflow_pressure,
        "demands": demands,
    }


def _trace_key(player_id: str) -> str:
    return f"wos:player:{player_id}:stamina_decisions"


def _verdict_payload(v: Verdict) -> dict[str, Any]:
    return {"id": v.demand_id, "sel": v.selected, "why": v.reason, "detail": v.detail}


def decision_payload(result: PlanResult, now: float) -> dict[str, Any]:
    """JSON-able snapshot of one decision for the UI decision trace."""
    d = result.decision
    return {
        "ts": now,
        "est": result.est,
        "period": result.period,
        "action": d.action,
        "reason": d.reason,
        "target": d.target_id,
        "priority": d.priority,
        "overflow": d.overflow_pressure,
        "verdicts": [_verdict_payload(v) for v in d.verdicts],
    }


async def write_decision_trace(
    redis: Redis,
    player_id: str,
    result: PlanResult,
    now: float,
) -> None:
    """Append the decision to a capped, TTL'd ring-buffer ZSET for the UI.

    Mirrors the scheduler's ``recent_runs`` write pattern (pipeline + prune +
    cap + expire). Best-effort — a Redis flap must not break planning.
    """
    key = _trace_key(player_id)
    member = json.dumps(decision_payload(result, now), separators=(",", ":"))
    try:
        pipe = redis.pipeline(transaction=True)
        pipe.zadd(key, {member: now})
        pipe.zremrangebyscore(key, "-inf", now - TRACE_RETENTION_SECONDS)
        pipe.zremrangebyrank(key, 0, -(TRACE_RETENTION_CAP + 1))
        pipe.expire(key, TRACE_RETENTION_SECONDS * 2)
        await pipe.execute()
    except Exception:
        logger.warning("stamina decision trace write failed for %s", player_id, exc_info=True)


async def enqueue_decision(
    queue: RedisQueue,
    *,
    instance_id: str,
    player_id: str,
    decision: Decision,
    period: str,
    now: float,
) -> bool:
    """Push the chosen scenario onto the queue. No-op for idle decisions.

    Tags the task with ``stamina_quota_id`` / ``stamina_period`` so the worker
    can increment the matching daily counter on success (see
    ``worker/instance_worker_tasks.py``) — that's how a consumed action is
    accounted against its quota.
    """
    if decision.action not in (CONSUME, SUPPLY) or not decision.task_type:
        return False
    # Demand priorities (10/60/100) are a relative ordering among stamina
    # consumers; the queue ranks on an absolute scale where ordinary tasks sit
    # at DEFAULT_PRIORITY (80_000). Lift the winner into that band so it isn't
    # buried — `+ priority` preserves the relative order between consumers.
    queue_priority = DEFAULT_PRIORITY + (decision.priority or 0)
    return await queue.schedule(
        task_id=f"stamina:{decision.target_id}:{player_id}:{int(now)}",
        player_id=player_id,
        task_type=decision.task_type,
        priority=queue_priority,
        run_at=now,
        instance_id=instance_id,
        dsl_scenario=decision.task_type,
        args={
            "stamina_quota_id": decision.target_id,
            "stamina_period": period,
            "stamina_delta": decision.stamina_delta,
        },
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )


async def prune_stale_quota(
    redis: Redis,
    player_id: str,
    state: dict[str, Any],
    period: str,
) -> None:
    """Drop quota counters from past game-days so the state hash stays bounded.

    ``state`` is the already-loaded hash, so finding stale fields is free; the
    HDEL only fires on a day boundary (when stale fields actually exist).
    """
    keep = f"quota:{period}:"
    stale = [
        k for k in state if k.startswith("quota:") and not k.startswith(keep)
    ]
    if not stale:
        return
    try:
        await redis.hdel(f"wos:player:{player_id}:state", *stale)
    except Exception:
        logger.warning("stamina quota prune failed for %s", player_id, exc_info=True)
