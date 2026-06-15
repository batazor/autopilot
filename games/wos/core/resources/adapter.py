"""Redis-backed adapter: bridge the pure allocator to live state + the queue.

Split so the decision logic stays unit-testable:

* :func:`build_world` / :func:`plan` are pure — they take a plain ``state`` dict
  (a decoded ``wos:player:<pid>:state`` hash), the decoded reservation ledger,
  and ``now``; they return the allocator :class:`~allocator.Decision`. No IO.
* :func:`read_ledger` / :func:`reserve` / :func:`release` / :func:`prune_ledger`
  manage the reservation ledger (the cross-resource hold that closes the
  dispatch→OCR race: a chosen action's whole cost vector is held with a TTL so
  the next tick doesn't double-allocate before the march shows on screen).
* :func:`write_decision_trace` / :func:`enqueue_decision` are the thin async
  side-effects (ring-buffer for the UI, queue push for the worker).

The scheduler calls these per active player each tick (see
``scheduler/runner.py:_run_resource_planner``), guarded by ``enabled``.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from games.wos.core.stamina.model import (
    estimate_stamina,
    quota_field,
    quota_period,
)

from layout.area_versions import eval_cond

from .allocator import (
    CONSUME,
    Action,
    ActionRuntime,
    Decision,
    Verdict,
    allocate,
)
from .model import (
    DEFAULT_SLOT_CAPACITY,
    ActionTable,
    WorldView,
)

if TYPE_CHECKING:
    from redis.asyncio import Redis

    from scheduler.queue import RedisQueue

logger = logging.getLogger(__name__)

DEFAULT_PRIORITY = 80_000
RESERVE_TTL_SECONDS = 90          # hold a chosen action's resources until the
                                  # march shows on screen (then the lease confirms)
TRACE_RETENTION_SECONDS = 24 * 3600
TRACE_RETENTION_CAP = 50

_TRUE_TOKENS = {"true", "yes", "on"}
_FALSE_TOKENS = {"false", "no", "off"}

_TABLE_CACHE: dict[str, tuple[float, ActionTable]] = {}


def load_table(path: str | Path | None = None) -> ActionTable:
    """``ActionTable.load()`` cached by file mtime — no disk read per tick."""
    from .model import DEFAULT_ACTIONS_PATH

    p = Path(path) if path else DEFAULT_ACTIONS_PATH
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _TABLE_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    table = ActionTable.load(p)
    _TABLE_CACHE[key] = (mtime, table)
    return table


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
    """Flat context for ``active_when``, with boolean-ish string flags
    normalised so ``"false"`` is falsy under eval_cond."""
    ctx: dict[str, Any] = {}
    for k, v in state.items():
        if isinstance(v, str) and v.strip().lower() in _TRUE_TOKENS:
            ctx[k] = 1
        elif isinstance(v, str) and v.strip().lower() in _FALSE_TOKENS:
            ctx[k] = 0
        else:
            ctx[k] = v
    return ctx


def _is_active(action: Action, ctx: dict[str, Any]) -> bool:
    if not action.active_when:
        return True
    return eval_cond(action.active_when, dict(ctx))


@dataclass(frozen=True, slots=True)
class PlanResult:
    """Outcome of one planning pass for a player."""

    world: WorldView
    period: str
    decision: Decision


# --- Live-state resolution (pure) --------------------------------------------


def _parse_hero_roster(state: dict[str, Any]) -> dict[str, list[str]]:
    """Free heroes by role from ``heroes.roster`` (a JSON list the reader writes).

    Contract (filled by the future ``sync_hero_roster`` scenario):
    ``heroes.roster`` = ``[{"id": "...", "role": "combat", "free": true}, ...]``.
    Returns ``{}`` when unread — the caller treats heroes as unobserved.
    """
    raw = state.get("heroes.roster")
    if not raw:
        return {}
    try:
        roster = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    by_role: dict[str, list[str]] = {}
    for h in roster or []:
        if not isinstance(h, dict) or not h.get("free", False):
            continue
        role = str(h.get("role") or "any")
        hid = str(h.get("id") or "")
        if hid:
            by_role.setdefault(role, []).append(hid)
    return by_role


def build_world(
    table: ActionTable,
    state: dict[str, Any],
    now: float,
    ledger: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> WorldView:
    """Resolve a decoded ``state`` hash + reservation ledger into a WorldView.

    Pure. In-flight occupancy and held reservations are subtracted so concurrent
    ticks (and the dispatch→OCR gap) can't over-allocate. Reserving a slot is the
    primary over-dispatch guard — every march also takes a slot, so the slot cap
    bounds concurrency even before troop/hero accounting lands.
    """
    specs = table.specs_by_id()

    # March slots: concurrency cap minus what's in flight minus held reservations.
    capacity = _to_int(state.get("marches.capacity")) or DEFAULT_SLOT_CAPACITY
    occupied = _to_int(state.get("marches.active_count"))
    held_slots = sum(int(r.get("slots", 0)) for r in ledger)
    slots_free = max(0, capacity - occupied - held_slots)

    # Stamina: interpolated estimate, minus stamina already promised by holds.
    stamina_est: float | None = None
    sspec = specs.get("stamina")
    if sspec is not None and sspec.observed:
        read_at = _to_float(state.get("stamina_at")) or now
        stamina_est = estimate_stamina(
            _to_float(state.get("stamina")),
            read_at,
            now,
            cap=sspec.cap or 200,
            regen_per_hour=sspec.regen_per_hour or 0.0,
        )
        if stamina_est is not None:
            held_stamina = sum(int(r.get("stamina", 0)) for r in ledger)
            stamina_est = max(0.0, stamina_est - held_stamina)

    # Troops (typed pool) — observed once sync_troop_pool writes the keys.
    tspec = specs.get("troops")
    troops_observed = bool(tspec and tspec.observed)
    troops_free: dict[str, int] = {}
    if troops_observed and tspec is not None:
        held_by_type: dict[str, int] = {}
        for r in ledger:
            for tt, amt in (r.get("troops") or {}).items():
                held_by_type[tt] = held_by_type.get(tt, 0) + int(amt)
        for tt in tspec.types:
            avail = _to_int(state.get(f"troops.{tt}.available"))
            troops_free[tt] = max(0, avail - held_by_type.get(tt, 0))

    # Heroes (exclusive set) — observed once sync_hero_roster writes the roster.
    hspec = specs.get("heroes")
    heroes_observed = bool(hspec and hspec.observed)
    free_heroes: dict[str, tuple[str, ...]] = {}
    if heroes_observed:
        held_heroes = {h for r in ledger for h in (r.get("heroes") or [])}
        for role, ids in _parse_hero_roster(state).items():
            free_heroes[role] = tuple(h for h in ids if h not in held_heroes)

    return WorldView(
        slots_capacity=capacity,
        slots_free=slots_free,
        stamina_est=stamina_est,
        troops_free=troops_free,
        troops_observed=troops_observed,
        free_heroes=free_heroes,
        heroes_observed=heroes_observed,
    )


def plan(
    table: ActionTable,
    state: dict[str, Any],
    now: float,
    ledger: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
) -> PlanResult:
    """Resolve live state into a WorldView + runtimes and run the allocator."""
    world = build_world(table, state, now, ledger)
    ctx = _eval_context(state)
    period = quota_period(now, table.daily_reset_utc)
    runtimes = [
        ActionRuntime(
            action=a,
            active=_is_active(a, ctx),
            quota_used=_to_int(state.get(quota_field(period, a.id))),
        )
        for a in table.actions
        if a.enabled
    ]
    decision = allocate(world, runtimes, table)
    return PlanResult(world=world, period=period, decision=decision)


# --- Reservation ledger (the cross-resource hold) ----------------------------


def _ledger_key(player_id: str) -> str:
    return f"wos:player:{player_id}:resource_reservations"


def _entry_active(entry: dict[str, Any], now: float) -> bool:
    """A reservation holds its resources iff:

    * confirmed (the march was seen in flight) and before its lease end
      (``expires_at`` = launch + the action's hours-long duration), OR
    * not yet confirmed but still within the short ``confirm_by`` window (the
      dispatch→OCR bridge). An unconfirmed entry past ``confirm_by`` is a failed
      dispatch → rolled back.
    """
    if entry.get("confirmed"):
        return float(entry.get("expires_at", 0)) > now
    return float(entry.get("confirm_by", entry.get("expires_at", 0))) > now


def filter_active_ledger(
    raw: dict[str, Any], now: float
) -> tuple[list[dict[str, Any]], list[str]]:
    """Split a decoded reservation hash into (active entries, expired field ids)."""
    active: list[dict[str, Any]] = []
    expired: list[str] = []
    for field_id, blob in raw.items():
        fid = field_id.decode() if isinstance(field_id, bytes) else str(field_id)
        text = blob.decode() if isinstance(blob, bytes) else blob
        try:
            entry = json.loads(text)
        except (TypeError, ValueError):
            expired.append(fid)
            continue
        if _entry_active(entry, now):
            active.append(entry)
        else:
            expired.append(fid)
    return active, expired


async def read_ledger(redis: Redis, player_id: str, now: float) -> list[dict[str, Any]]:
    """Active (non-expired) reservations for a player; prunes expired in passing."""
    raw = await redis.hgetall(_ledger_key(player_id))
    active, expired = filter_active_ledger(raw or {}, now)
    if expired:
        await redis.hdel(_ledger_key(player_id), *expired)
    return active


def reservation_id(decision: Decision, now: float) -> str:
    return f"{decision.target_id}:{int(now)}"


def reservation_entry(
    decision: Decision,
    now: float,
    confirm_ttl: float = RESERVE_TTL_SECONDS,
    lease_seconds: float | None = None,
) -> dict[str, Any]:
    """The cost bundle a chosen action holds, as a JSON-able ledger entry.

    Two timescales: ``confirm_by`` (short — the march must show on screen within
    this or the hold is rolled back) and ``expires_at`` (the full lease — gathering
    holds its slot/troops/heroes for HOURS). ``lease_seconds`` defaults to the
    action's declared duration; until confirmed, only ``confirm_by`` keeps it live.
    """
    asg = decision.assignment
    lease = decision.lease_seconds if lease_seconds is None else lease_seconds
    return {
        "id": reservation_id(decision, now),
        "action_id": decision.target_id,
        "slots": decision.slot_cost,
        "stamina": -decision.stamina_delta,
        "troops": dict(asg.troops) if asg else {},
        "heroes": list(asg.heroes) if asg else [],
        "created_at": now,
        "confirm_by": now + confirm_ttl,
        "expires_at": now + max(float(lease), confirm_ttl),
        "lease_seconds": lease,
        "confirmed": False,
    }


async def reserve(
    redis: Redis,
    player_id: str,
    decision: Decision,
    now: float,
    confirm_ttl: float = RESERVE_TTL_SECONDS,
) -> str | None:
    """Atomically hold the chosen action's whole cost vector in the ledger."""
    if decision.action != CONSUME or not decision.target_id:
        return None
    entry = reservation_entry(decision, now, confirm_ttl)
    await redis.hset(_ledger_key(player_id), entry["id"], json.dumps(entry))
    return entry["id"]


async def confirm_reservation(
    redis: Redis, player_id: str, res_id: str, *, ends_at: float
) -> bool:
    """Mark a reservation confirmed (march seen in flight) and set its lease end to
    the observed march timer ``ends_at`` — so the slot/troops/heroes stay held for
    the real duration, not the short dispatch window."""
    raw = await redis.hget(_ledger_key(player_id), res_id)
    if not raw:
        return False
    text = raw.decode() if isinstance(raw, bytes) else raw
    entry = json.loads(text)
    entry["confirmed"] = True
    entry["expires_at"] = float(ends_at)
    await redis.hset(_ledger_key(player_id), res_id, json.dumps(entry))
    return True


async def release(redis: Redis, player_id: str, res_id: str) -> None:
    await redis.hdel(_ledger_key(player_id), res_id)


def seconds_until_slot_frees(ledger: list[dict[str, Any]], now: float) -> float | None:
    """When the next confirmed slot-holding lease ends (for opportunity-cost / TTL).

    ``None`` if nothing is holding a slot. Lets the planner back off until a slot
    frees instead of re-polling — a 6h gather won't free a march for 6h.
    """
    ends = [
        float(e.get("expires_at", 0)) - now
        for e in ledger
        if e.get("confirmed") and int(e.get("slots", 0)) > 0
    ]
    return min(ends) if ends else None


# --- Decision trace (ring-buffer for the UI) ---------------------------------


def decision_signature(decision: Decision) -> str:
    """Identity of a decision, ignoring moving estimates — lets the planner skip
    rewriting the trace when its decision is unchanged tick-to-tick."""
    return f"{decision.action}|{decision.target_id}|{decision.reason}"


def _trace_key(player_id: str) -> str:
    return f"wos:player:{player_id}:resource_decisions"


def _verdict_payload(v: Verdict) -> dict[str, Any]:
    return {"id": v.action_id, "sel": v.selected, "why": v.reason, "detail": v.detail}


def decision_payload(result: PlanResult, now: float) -> dict[str, Any]:
    """JSON-able snapshot of one decision for the UI decision trace."""
    d = result.decision
    asg = d.assignment
    return {
        "ts": now,
        "period": result.period,
        "action": d.action,
        "reason": d.reason,
        "target": d.target_id,
        "priority": d.priority,
        "slots_free": result.world.slots_free,
        "slots_capacity": result.world.slots_capacity,
        "stamina_est": result.world.stamina_est,
        "assignment": {
            "heroes": list(asg.heroes) if asg else [],
            "troops": dict(asg.troops) if asg else {},
        },
        "verdicts": [_verdict_payload(v) for v in d.verdicts],
    }


async def write_decision_trace(
    redis: Redis, player_id: str, result: PlanResult, now: float
) -> None:
    """Append the decision to a capped, TTL'd ring-buffer ZSET for the UI.

    Best-effort — a Redis flap must not break planning.
    """
    key = _trace_key(player_id)
    member = json.dumps(decision_payload(result, now))
    try:
        pipe = redis.pipeline(transaction=False)
        pipe.zadd(key, {member: now})
        pipe.zremrangebyscore(key, 0, now - TRACE_RETENTION_SECONDS)
        pipe.zremrangebyrank(key, 0, -(TRACE_RETENTION_CAP + 1))
        pipe.expire(key, TRACE_RETENTION_SECONDS)
        await pipe.execute()
    except Exception:
        logger.debug("resource decision trace write failed", exc_info=True)


async def enqueue_decision(
    queue: RedisQueue,
    *,
    instance_id: str,
    player_id: str,
    decision: Decision,
    period: str,
    reservation: str | None,
    now: float,
) -> bool:
    """Push the chosen scenario onto the queue. No-op for idle decisions.

    Carries the reservation id + concrete assignment (which heroes, troop counts)
    so the march scenario staffs exactly what was held, plus the quota markers the
    worker increments on success.
    """
    if decision.action != CONSUME or not decision.task_type:
        return False
    asg = decision.assignment
    queue_priority = DEFAULT_PRIORITY + (decision.priority or 0)
    return await queue.schedule(
        task_id=f"resource:{decision.target_id}:{player_id}:{int(now)}",
        player_id=player_id,
        task_type=decision.task_type,
        priority=queue_priority,
        run_at=now,
        instance_id=instance_id,
        dsl_scenario=decision.task_type,
        args={
            "resource_action_id": decision.target_id,
            "resource_period": period,
            "resource_reservation": reservation,
            "assign_heroes": list(asg.heroes) if asg else [],
            "assign_troops": dict(asg.troops) if asg else {},
            "stamina_delta": decision.stamina_delta,
        },
        skip_if_duplicate=True,
        dedup_ignore_region=True,
    )
