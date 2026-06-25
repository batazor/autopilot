"""MARCH-channel arbitration — who gets a march slot this tick.

The march slots are the bot's most contended channel: intel runs, resource
gathers and (later) raids all want them. Each domain proposes its own
:class:`CandidateAction`s; this module assembles the ones that compete for MARCH
and hands them to :func:`coordinator.coordinate`, which fills each idle slot with
the highest-priority action the shared budget (stamina + resources) still covers.

The priority bands (see :mod:`objective`) put a quick, expiring intel run above a
boosted gather, so a free-loot intel marker preempts a long gather — but intel
costs stamina, so when the pool is short it *starves* at the coordinator and the
slot falls through to the (free) gather instead, with stamina reported as the
bottleneck for the economy loop.

Pure composition over the existing pieces (``from_intel_plan`` + ``economy_bias``
/ ``gather_candidates`` + ``coordinate``); no IO. Live readers (idle-slot count,
resource balances) and dispatch of the winning commits are the caller's job.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .adapters import from_intel_plan
from .allocate import coordinate_optimal
from .coordinator import coordinate
from .economy import economy_bias, gather_candidates
from .model import MARCH, CandidateAction, Channel, Utility
from .objective import domain_priority

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from games.wos.core.roles import RoleProfile
    from games.wos.intel.planner import IntelPlan

    from .model import CoordinatorDecision

# Stamina one intel marker costs — mirrors intel.planner.DEFAULT_COST_PER_EVENT
# and budget.yaml's intel_events demand. Kept local so this pure module doesn't
# import the intel package; callers that know the live value pass ``cost=``.
_INTEL_MARKER_COST = 10


def march_channels(idle_slots: int) -> list[Channel]:
    """``idle_slots`` free MARCH lanes with stable ids (``march_1`` …)."""
    return [Channel(id=f"march_{i + 1}", kind=MARCH) for i in range(max(0, int(idle_slots)))]


def intel_intent(
    *,
    stamina: float | None,
    seconds_since_last_run: float | None,
    cost: int = _INTEL_MARKER_COST,
    reserve: int = 0,
    cooldown_s: float = 0.0,
    role: RoleProfile | None = None,
    boost: float = 1.0,
) -> CandidateAction | None:
    """A "blind" intel MARCH candidate — intel wants a slot, board unread.

    Dispatch-blind: we don't read the markers here. ``intel_run`` reads them live
    when it runs and the tap-gate (``select_planned_marker``) declines per-marker,
    so this only gates on cheap signals — at least one marker's stamina is
    affordable after the (calendar-driven) event reserve, and the board-refresh
    cooldown has elapsed since the last run. Returns ``None`` to skip intel this
    tick. The caller feeds the result to :func:`plan_march` as an extra candidate
    so intel still contends with gather on the channel.
    """
    if stamina is None:
        return None
    if seconds_since_last_run is not None and seconds_since_last_run < cooldown_s:
        return None
    if (stamina - max(0, int(reserve))) < cost:
        return None
    return CandidateAction(
        domain="intel",
        channel_kind=MARCH,
        key="intel:run",
        utility=Utility(base_value=domain_priority("intel", role, boost=boost)),
        cost={"stamina": int(cost)},
        detail="intel run (blind)",
    )


def timed_event_intent(
    domain: str,
    *,
    active: bool,
    attempts_left: int | None,
    cost: Mapping[str, int] | None = None,
    key: str | None = None,
    role: RoleProfile | None = None,
    boost: float = 1.0,
) -> CandidateAction | None:
    """A MARCH candidate for a time-limited event that spends a march.

    Generic over events like Romance Season: eligible only while the event is
    ``active`` (its TTL window is open) and it still has ``attempts_left`` for the
    day (``None`` = unknown → allow; ``<= 0`` = exhausted → skip). The attack
    spends a march slot but not the shared resource pool, so ``cost`` defaults to
    empty (it never starves on resources — it just needs a free slot). Returns
    ``None`` to skip the event this tick.
    """
    if not active:
        return None
    if attempts_left is not None and attempts_left <= 0:
        return None
    return CandidateAction(
        domain=domain,
        channel_kind=MARCH,
        key=key or f"{domain}:run",
        utility=Utility(base_value=domain_priority(domain, role, boost=boost)),
        cost=dict(cost or {}),
        detail=f"{domain} (event)",
    )


def plan_march(
    *,
    idle_slots: int,
    balances: Mapping[str, int],
    intel_plan: IntelPlan | None = None,
    role: RoleProfile | None = None,
    bottleneck: Sequence[str] = (),
    caps: Mapping[str, int] | None = None,
    min_buffer: Mapping[str, int] | None = None,
    boosts: Mapping[str, float] | None = None,
    extra_candidates: Sequence[CandidateAction] = (),
    optimize: bool = True,
) -> CoordinatorDecision:
    """Arbitrate the idle MARCH slots across intel + gather (+ ``extra``).

    Builds the competing MARCH candidates — the intel batch (priced in stamina)
    and economy-driven gather targets (free, lifted while a resource is short) —
    plus any ``extra_candidates`` (e.g. raids, once a raids planner exists), then
    runs :func:`coordinate` over ``idle_slots`` lanes and the shared ``balances``.

    ``bottleneck`` / ``caps`` / ``min_buffer`` shape the gather targets via
    :func:`economy_bias`; ``boosts`` is the calendar bias applied to the intel
    band. Returns the full decision (commits to dispatch, what starved, the
    bottleneck resources) — the caller turns each MARCH commit into a queued
    scenario.
    """
    candidates: list[CandidateAction] = []
    if intel_plan is not None:
        candidates.extend(from_intel_plan(intel_plan, role=role, boosts=boosts))

    bias = economy_bias(
        balances,
        bottleneck=bottleneck,
        caps=caps,
        min_buffer=min_buffer,
        role=role,
    )
    candidates.extend(gather_candidates(bias, role=role))
    candidates.extend(extra_candidates)

    solver = coordinate_optimal if optimize else coordinate
    return solver(march_channels(idle_slots), candidates, balances)
