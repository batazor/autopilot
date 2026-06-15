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

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .march import intel_intent, plan_march
from .model import MARCH

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile
    from redis.asyncio import Redis

    from scheduler.queue import RedisQueue

    from .model import CoordinatorDecision

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
) -> MarchDispatch:
    """Dispatch-blind MARCH tick: decide + queue without reading the intel board.

    Reads the player's stamina estimate + the calendar-driven event reserve, gates
    intel on stamina + the board-refresh cooldown, then routes the blind intent
    through :func:`plan_march` (so it still contends with gather on the channel)
    and dispatches the winners. ``idle_slots`` and ``resource_balances`` are
    injected — the caller's live readers (the march-lease slot ledger and resource
    OCR); with no resource balances, gather simply doesn't compete yet.

    Returns the dispatch trace. No-op (empty dispatch) when stamina is unknown,
    the reserve eats the budget, or the cooldown hasn't elapsed.
    """
    state = await _read_player_state(redis, player_id)
    stamina = _to_float(state.get("stamina", ""))
    reserve = _intel_reserve(state)
    secs_since = await _seconds_since_last_intel(
        queue, instance_id=instance_id, player_id=player_id, now=now
    )

    intent = intel_intent(
        stamina=stamina,
        seconds_since_last_run=secs_since,
        reserve=reserve,
        cooldown_s=cooldown_s,
        role=role,
        boost=(boosts or {}).get("intel", 1.0),
    )
    balances: dict[str, int] = {"stamina": int(stamina or 0), **(resource_balances or {})}
    decision = plan_march(
        idle_slots=idle_slots,
        balances=balances,
        role=role,
        boosts=boosts,
        extra_candidates=(intent,) if intent else (),
    )
    return await dispatch_march(
        decision,
        queue=queue,
        instance_id=instance_id,
        player_id=player_id,
        now=now,
    )
