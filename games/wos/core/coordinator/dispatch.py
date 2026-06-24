"""Turn a coordinator MARCH decision into queued scenarios — the IO boundary.

Everything else in this package is pure (decide what *should* run). This module
is the thin async side-effect: given the :class:`CoordinatorDecision` from
:func:`march.plan_march`, it pushes one queue task per committed MARCH slot so the
worker actually runs it.

Mirrors ``stamina.adapter.enqueue_decision`` (the proven consumer-scenario push):
``task_type`` / ``dsl_scenario`` are the scenario key, ``skip_if_duplicate`` keeps
a single run of each scenario in flight, and the cross-domain priority is lifted
into the queue's absolute band (ordinary tasks sit at 80_000) so the winner isn't
buried while preserving the relative order the coordinator chose.

Overlap note: ``stamina.adapter`` also enqueues ``intel_run`` from the demand
table. That allocator is OFF (``budget.yaml enabled: false``), so there's no live
double-dispatch today; the coordinator is the intended owner of MARCH dispatch.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .march import intel_intent, plan_march, timed_event_intent
from .model import MARCH

# Per-deployment override / kill-switch for the march planner, independent of the
# committed march.yaml default. Set WOS_MARCH_ENABLED=false to disable a live
# config without editing the file (or =true to enable a dormant one).
_MARCH_ENABLED_ENV = "WOS_MARCH_ENABLED"
_TRUTHY = {"1", "true", "yes", "on"}

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile
    from redis.asyncio import Redis

    from scheduler.queue import RedisQueue

    from .model import CandidateAction, CoordinatorDecision

logger = logging.getLogger(__name__)

# Ordinary queue tasks sit at this absolute priority (see
# ``stamina.adapter.DEFAULT_PRIORITY``); a MARCH winner is lifted to
# ``BASE + cross-domain priority`` so it ranks above background work while the
# relative order between MARCH domains (intel > gather) is preserved.
DISPATCH_PRIORITY_BASE = 80_000

# Min gap between blind intel dispatches. The board refreshes on a (multi-hour)
# timer, so re-running right after a clear just burns a navigation. Placeholder —
# tune to the real refresh cadence once known.
INTEL_RUN_COOLDOWN_S = 900.0

_MARCH_CONFIG_PATH = Path(__file__).resolve().parent / "march.yaml"
_MARCH_CONFIG_CACHE: dict[str, tuple[float, MarchConfig]] = {}


@dataclass(frozen=True, slots=True)
class MarchConfig:
    """The autonomous MARCH planner's switch + knobs (``march.yaml``)."""

    enabled: bool = False
    intel_cooldown_s: float = INTEL_RUN_COOLDOWN_S


def _parse_march_config(path: Path) -> MarchConfig:
    import yaml

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("march config read failed at %s", path, exc_info=True)
        return MarchConfig()
    if not isinstance(raw, dict):
        return MarchConfig()
    return MarchConfig(
        enabled=bool(raw.get("enabled", False)),
        intel_cooldown_s=float(raw.get("intel_cooldown_s", INTEL_RUN_COOLDOWN_S)),
    )


def load_march_config(path: str | Path | None = None) -> MarchConfig:
    """``march.yaml`` parsed + cached by mtime (no disk read per scheduler tick;
    edits still picked up). Independent of resources/actions.yaml ``enabled``."""
    p = Path(path) if path else _MARCH_CONFIG_PATH
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    hit = _MARCH_CONFIG_CACHE.get(key)
    if hit is not None and hit[0] == mtime:
        cfg = hit[1]
    else:
        cfg = _parse_march_config(p)
        _MARCH_CONFIG_CACHE[key] = (mtime, cfg)
    # Env override re-checked each call (not cached) so the kill-switch takes
    # effect without a file touch.
    override = os.environ.get(_MARCH_ENABLED_ENV)
    if override is not None and override.strip():
        return replace(cfg, enabled=override.strip().lower() in _TRUTHY)
    return cfg


@dataclass(frozen=True, slots=True)
class MarchScenario:
    """The scenario a committed MARCH domain dispatches to."""

    task_type: str
    dsl_scenario: str


# Domain → scenario. Only domains with a real, runnable scenario appear here;
# others (e.g. ``gather`` until the gathering module is enabled) are reported as
# skipped rather than silently dropped.
MARCH_SCENARIOS: dict[str, MarchScenario] = {
    "intel": MarchScenario(task_type="intel_run", dsl_scenario="intel_run"),
    "romance_season": MarchScenario(
        task_type="event.romance_season", dsl_scenario="event.romance_season"
    ),
}


@dataclass(frozen=True, slots=True)
class MarchEnqueue:
    domain: str
    task_type: str
    channel_id: str
    priority: int


@dataclass(frozen=True, slots=True)
class MarchSkip:
    domain: str
    key: str
    reason: str          # no_scenario | duplicate


@dataclass(frozen=True, slots=True)
class MarchDispatch:
    """What this pass actually queued, and what it didn't (for the trace)."""

    enqueued: tuple[MarchEnqueue, ...]
    skipped: tuple[MarchSkip, ...]


# Decision-trace ring buffer — mirrors stamina/resources adapters so `botctl why`
# / `botctl planners` can surface what march last decided (per player).
TRACE_RETENTION_SECONDS = 24 * 3600
TRACE_RETENTION_CAP = 50

# Per-player signature of the last march outcome we traced. The scheduler is one
# long-lived process, so an in-memory gate (like the runner's stamina/resource
# sig maps) keeps the ring buffer to real changes instead of one row per tick.
_MARCH_TRACE_SIG: dict[str, str] = {}


def march_trace_key(player_id: str) -> str:
    return f"wos:player:{player_id}:march_decisions"


def _march_reason(dispatch: MarchDispatch, *, idle_slots: int, had_candidates: bool) -> str:
    if dispatch.enqueued:
        return "queued " + ",".join(e.domain for e in dispatch.enqueued)
    if idle_slots <= 0:
        return "нет свободных march-слотов"
    if not had_candidates:
        return "нет кандидатов (stamina/cooldown/reserve)"
    if dispatch.skipped and all(s.reason == "duplicate" for s in dispatch.skipped):
        return "кандидаты уже в полёте"
    return "ничего не закоммичено"


def march_decision_payload(
    dispatch: MarchDispatch, *, idle_slots: int, stamina: float | None, had_candidates: bool, now: float
) -> dict[str, Any]:
    """JSON-able snapshot of one march tick (shape aligns with stamina/resources)."""
    return {
        "ts": now,
        "action": "dispatch" if dispatch.enqueued else "idle",
        "reason": _march_reason(dispatch, idle_slots=idle_slots, had_candidates=had_candidates),
        "target": dispatch.enqueued[0].domain if dispatch.enqueued else "",
        "idle_slots": int(idle_slots),
        "stamina_est": stamina,
        "enqueued": [
            {"domain": e.domain, "task": e.task_type, "priority": e.priority}
            for e in dispatch.enqueued
        ],
        "skipped": [{"domain": s.domain, "reason": s.reason} for s in dispatch.skipped],
    }


def _march_signature(dispatch: MarchDispatch, idle_slots: int) -> str:
    enq = ",".join(sorted(e.domain for e in dispatch.enqueued))
    skp = ",".join(sorted(f"{s.domain}:{s.reason}" for s in dispatch.skipped))
    return f"{'1' if dispatch.enqueued else '0'}|{enq}|{skp}|{'slots' if idle_slots > 0 else 'full'}"


async def write_march_trace(
    redis: Redis,
    player_id: str,
    dispatch: MarchDispatch,
    *,
    idle_slots: int,
    stamina: float | None,
    had_candidates: bool,
    now: float,
) -> None:
    """Append a march decision to the per-player ring buffer ZSET (signature-gated).

    Best-effort and deduped on outcome change — a Redis flap or an unchanged tick
    must never affect dispatch.
    """
    sig = _march_signature(dispatch, idle_slots)
    if _MARCH_TRACE_SIG.get(player_id) == sig:
        return
    key = march_trace_key(player_id)
    payload = march_decision_payload(
        dispatch, idle_slots=idle_slots, stamina=stamina, had_candidates=had_candidates, now=now
    )
    member = json.dumps(payload, separators=(",", ":"))
    try:
        pipe = redis.pipeline(transaction=False)
        pipe.zadd(key, {member: now})
        pipe.zremrangebyscore(key, 0, now - TRACE_RETENTION_SECONDS)
        pipe.zremrangebyrank(key, 0, -(TRACE_RETENTION_CAP + 1))
        pipe.expire(key, TRACE_RETENTION_SECONDS)
        await pipe.execute()
        _MARCH_TRACE_SIG[player_id] = sig
    except Exception:
        logger.debug("march decision trace write failed", exc_info=True)


async def dispatch_march(
    decision: CoordinatorDecision,
    *,
    queue: RedisQueue,
    instance_id: str,
    player_id: str,
    now: float,
    scenarios: Mapping[str, MarchScenario] = MARCH_SCENARIOS,
) -> MarchDispatch:
    """Queue one scenario per committed MARCH slot.

    Each MARCH commit's domain is mapped to its scenario; domains without one are
    skipped (``no_scenario``). ``skip_if_duplicate`` means a domain already in
    flight is skipped (``duplicate``) and re-queued on a later tick — so a
    multi-slot commit collapses to one run-per-domain-in-flight, which matches the
    worker's serial per-instance execution.
    """
    enqueued: list[MarchEnqueue] = []
    skipped: list[MarchSkip] = []
    for commit in decision.committed_for(MARCH):
        action = commit.action
        spec = scenarios.get(action.domain)
        if spec is None:
            skipped.append(MarchSkip(action.domain, action.key, "no_scenario"))
            continue
        priority = DISPATCH_PRIORITY_BASE + int(action.priority)
        ok = await queue.schedule(
            task_id=f"march:{action.domain}:{action.key}:{int(now)}",
            player_id=player_id,
            task_type=spec.task_type,
            priority=priority,
            run_at=now,
            instance_id=instance_id,
            dsl_scenario=spec.dsl_scenario,
            args={"march_domain": action.domain, "march_channel": commit.channel_id},
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )
        if ok:
            enqueued.append(MarchEnqueue(action.domain, spec.task_type, commit.channel_id, priority))
        else:
            skipped.append(MarchSkip(action.domain, action.key, "duplicate"))
    return MarchDispatch(enqueued=tuple(enqueued), skipped=tuple(skipped))


def _decode(raw: Any) -> str:
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    return "" if raw is None else str(raw)


def _to_float(text: str) -> float | None:
    if text in ("", None):
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _to_int(text: str) -> int | None:
    f = _to_float(text)
    return int(f) if f is not None else None


def _romance_intent(
    state: Mapping[str, str],
    *,
    role: RoleProfile | None,
    boost: float,
) -> CandidateAction | None:
    """Romance Season MARCH candidate from the scenario's read-and-cached state.

    The ``event.romance_season`` scenario writes ``ttl_remaining_s`` (window open
    while > 0) and ``attack_count`` (attempts left today, capped at 5) into the
    player-state hash. Active + attempts → it competes for a march slot, banded
    just below intel. NOTE: ``ttl_remaining_s`` is the last on-screen read (it
    doesn't decay between reads); the durable ``event_timer`` (config.event_timers,
    ``event_timer: romance_season``) is the authoritative TTL for a follow-up.
    """
    ttl = _to_float(state.get("events.romanceSeason.ttl_remaining_s", ""))
    attempts = _to_int(state.get("events.romanceSeason.attack_count", ""))
    return timed_event_intent(
        "romance_season",
        active=ttl is not None and ttl > 0,
        attempts_left=attempts,
        role=role,
        boost=boost,
    )


async def _read_player_state(redis: Redis, player_id: str) -> dict[str, str]:
    try:
        raw = await redis.hgetall(f"wos:player:{player_id}:state")
    except Exception:
        logger.debug("march tick: state read failed player=%s", player_id, exc_info=True)
        return {}
    return {_decode(k): _decode(v) for k, v in (raw or {}).items()}


def _intel_reserve(state: Mapping[str, str]) -> int:
    """Calendar-driven event reserve for intel (same rule the tap handler uses)."""
    try:
        from games.wos.core.stamina.adapter import load_budget
        from games.wos.core.stamina.model import reserve_for

        return reserve_for(load_budget(), "intel_events", dict(state))
    except Exception:
        logger.debug("march tick: reserve derivation failed", exc_info=True)
        return 0


async def _seconds_since_last_intel(
    queue: RedisQueue, *, instance_id: str, player_id: str, now: float
) -> float | None:
    try:
        last = await queue.last_run_at(
            instance_id=instance_id, task_type="intel_run", player_id=player_id
        )
    except Exception:
        logger.debug("march tick: last_run_at read failed", exc_info=True)
        return None
    return (now - float(last)) if last is not None else None


async def run_march_tick(
    *,
    queue: RedisQueue,
    redis: Redis,
    instance_id: str,
    player_id: str,
    now: float,
    idle_slots: int,
    resource_balances: Mapping[str, int] | None = None,
    role: RoleProfile | None = None,
    cooldown_s: float = INTEL_RUN_COOLDOWN_S,
    boosts: Mapping[str, float] | None = None,
    state: Mapping[str, str] | None = None,
) -> MarchDispatch:
    """Dispatch-blind MARCH tick: decide + queue without reading the intel board.

    Reads the player's stamina estimate + the calendar-driven event reserve, gates
    intel on stamina + the board-refresh cooldown, then routes the blind intent
    through :func:`plan_march` (so it still contends with gather on the channel)
    and dispatches the winners. ``idle_slots`` and ``resource_balances`` are
    injected — the caller's live readers (the march-lease slot ledger and resource
    OCR); with no resource balances, gather simply doesn't compete yet. ``state``
    may be passed (the scheduler already decoded the player-state hash) to skip a
    redundant read.

    Returns the dispatch trace. No-op (empty dispatch) when stamina is unknown,
    the reserve eats the budget, or the cooldown hasn't elapsed.
    """
    if state is None:
        state = await _read_player_state(redis, player_id)
    stamina = _to_float(state.get("stamina", ""))
    reserve = _intel_reserve(state)
    # Pace blind intel re-runs by the live board-refresh timer ("Refreshes in:
    # HH:MM:SS", stored as seconds by intel_run) — don't re-run until the board
    # has actually refreshed. Falls back to the static cooldown placeholder
    # until the timer has been read at least once.
    board_ttl = _to_float(state.get("intel.refresh_in", ""))
    if board_ttl is not None and board_ttl > 0:
        cooldown_s = board_ttl
    secs_since = await _seconds_since_last_intel(
        queue, instance_id=instance_id, player_id=player_id, now=now
    )

    # MARCH-spending candidates whose eligibility isn't a live board read:
    # the blind intel run + any time-limited events (Romance Season, …). They
    # compete with gather inside plan_march; coordinate() fills the idle slots.
    candidates = [
        intel_intent(
            stamina=stamina,
            seconds_since_last_run=secs_since,
            reserve=reserve,
            cooldown_s=cooldown_s,
            role=role,
            boost=(boosts or {}).get("intel", 1.0),
        ),
        _romance_intent(state, role=role, boost=(boosts or {}).get("romance_season", 1.0)),
    ]
    extras = tuple(c for c in candidates if c is not None)

    balances: dict[str, int] = {"stamina": int(stamina or 0), **(resource_balances or {})}
    decision = plan_march(
        idle_slots=idle_slots,
        balances=balances,
        role=role,
        boosts=boosts,
        extra_candidates=extras,
    )
    dispatch = await dispatch_march(
        decision,
        queue=queue,
        instance_id=instance_id,
        player_id=player_id,
        now=now,
    )
    await write_march_trace(
        redis,
        player_id,
        dispatch,
        idle_slots=idle_slots,
        stamina=stamina,
        had_candidates=bool(extras),
        now=now,
    )
    return dispatch
