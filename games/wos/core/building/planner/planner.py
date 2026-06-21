"""Furnace-first build planner: decide which building to upgrade next.

Pure decision function over the static :class:`~model.BuildGraph` plus the
player's current building levels. The Furnace is the spearhead — we always try
to push it one level; when its next level is gated by a prerequisite that isn't
met yet, we recurse into that prerequisite (and so on), returning the *deepest
ready* upgrade on the path. That's the single building to build now.

Why this works from the data alone: each level's ``prerequisites`` encode both
the explicit deps (Furnace 11 ← Embassy 10) and the universal cap (Embassy 9 ←
Furnace Lv. 9), so resolving them recursively yields the canonical furnace-first
order with almost no hand-authored policy.

The live levels reader and the navigate-and-tap execution are deferred (the bot
can't read per-building levels yet); this module only answers "what next?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import policy
from .model import level_rank

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import BuildGraph, LevelReq

# --- Plan reasons ------------------------------------------------------------
SELECTED = "selected"            # step is the building to upgrade now
GOAL_REACHED = "goal_reached"    # goal building already at/above the cap
GOAL_UNKNOWN = "goal_unknown"    # goal id not in the graph
BLOCKED = "blocked"              # next goal level is gated by an unbuildable prereq

# Default v1 horizon: plan up to Furnace 30 (Fire-Crystal levels are a follow-up).
DEFAULT_GOAL = "furnace"
DEFAULT_GOAL_CAP = 30.0


@dataclass(frozen=True, slots=True)
class BuildStep:
    """The single upgrade the planner picked this pass."""

    building_id: str
    from_rank: float
    to_level: str
    to_rank: float


@dataclass(frozen=True, slots=True)
class BuildPlan:
    """The chosen step plus the dependency chain that led to it (for the trace)."""

    step: BuildStep | None
    reason: str
    chain: tuple[str, ...] = field(default_factory=tuple)   # goal → … → chosen
    detail: str = ""
    affordable: bool = True                                 # enough resources for `step`?
    shortfall: tuple[tuple[str, int], ...] = ()             # (item, amount short)


def _shortfall(
    cost: tuple[tuple[str, int], ...], resources: Mapping[str, Any]
) -> tuple[tuple[str, int], ...]:
    """Per-item resource gap for ``cost`` against current ``resources`` balances.

    Keys are whatever ``build_cost`` uses (item-icon ids like ``item_icon_103``);
    the balance reader must supply matching keys. Empty → affordable.
    """
    out: list[tuple[str, int]] = []
    for item, amount in cost:
        have = int(resources.get(item, 0) or 0)
        if have < amount:
            out.append((item, amount - have))
    return tuple(out)


def current_rank(levels: Mapping[str, Any], building_id: str) -> float:
    """Player's current rank for a building (0 = not built)."""
    return level_rank(levels.get(building_id, 0))


def _resolve(
    graph: BuildGraph,
    levels: Mapping[str, Any],
    building_id: str,
    visiting: frozenset[str],
) -> tuple[BuildStep | None, str, list[str]]:
    """Find the deepest ready upgrade needed to advance ``building_id`` one level."""
    if building_id in visiting:
        return None, BLOCKED, [building_id]           # cycle guard
    spec = graph.spec(building_id)
    if spec is None:
        return None, BLOCKED, [building_id]
    cur = current_rank(levels, building_id)
    nxt = spec.next_after(cur)
    if nxt is None:
        return None, GOAL_REACHED, [building_id]      # already maxed

    blocked = False
    for pre_id, pre_rank in nxt.prereqs:
        if current_rank(levels, pre_id) >= pre_rank:
            continue                                   # prereq satisfied
        if graph.spec(pre_id) is None:
            continue                                   # unknown dep → don't hard-block
        sub_step, sub_reason, sub_chain = _resolve(
            graph, levels, pre_id, visiting | {building_id}
        )
        if sub_step is not None:
            return sub_step, sub_reason, [building_id, *sub_chain]
        blocked = True                                 # this prereq can't advance now

    if blocked:
        return None, BLOCKED, [building_id]
    step = BuildStep(building_id, cur, nxt.level, nxt.rank)
    return step, SELECTED, [building_id]


def plan_next(
    graph: BuildGraph,
    levels: Mapping[str, Any],
    *,
    goal_id: str = DEFAULT_GOAL,
    goal_cap: float = DEFAULT_GOAL_CAP,
    resources: Mapping[str, Any] | None = None,
) -> BuildPlan:
    """Pick the next building to upgrade under a furnace-first policy.

    ``levels`` maps ``building_id`` → current level (int / "10" / "FC-3"); missing
    means not built. When ``resources`` (current balances) is given, the chosen
    step's ``build_cost`` is checked against it: ``affordable`` / ``shortfall``
    report whether the player can pay now (the dependency pick is unchanged — the
    target is still correct, the bot just gathers/waits when short).

    Returns the deepest ready upgrade toward the goal, or a non-``SELECTED`` reason
    (goal reached / unknown / blocked).
    """
    spec = graph.spec(goal_id)
    if spec is None:
        return BuildPlan(None, GOAL_UNKNOWN)
    if current_rank(levels, goal_id) >= goal_cap:
        return BuildPlan(None, GOAL_REACHED, (goal_id,))
    step, reason, chain = _resolve(graph, levels, goal_id, frozenset())

    shortfall: tuple[tuple[str, int], ...] = ()
    if step is not None and resources is not None:
        target = graph.spec(step.building_id)
        lvl = target.level(step.to_level) if target else None
        if lvl is not None:
            shortfall = _shortfall(lvl.cost, resources)
    return BuildPlan(
        step=step,
        reason=reason,
        chain=tuple(chain),
        affordable=not shortfall,
        shortfall=shortfall,
    )


# --- Multi-track value-greedy planner (fills N construction queues) ----------
INSUFFICIENT_RESOURCES = "insufficient_resources"   # ready candidates exist, none affordable
ALL_MAXED = "all_maxed"                              # nothing left to build


@dataclass(frozen=True, slots=True)
class BuildCandidate:
    """One buildable upgrade across any track, with its value and affordability."""

    instance_id: str          # plot id ("shelter_3") — what gets built
    spec_id: str              # db spec id ("shelter")
    track: str                # progression | bottleneck | economy | camp
    to_level: str
    to_rank: float
    value: float
    cost_total: int
    affordable: bool
    time_s: int = 0           # construction time (for the queue-rental ROI calc)
    shortfall: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True, slots=True)
class BuildSlate:
    """What to build this pass: one pick per free queue, plus the ranked trace."""

    picks: tuple[BuildCandidate, ...]
    candidates: tuple[BuildCandidate, ...]
    reason: str


def _instance_next(
    graph: BuildGraph, levels: Mapping[str, Any], spec_id: str, instance_id: str
) -> LevelReq | None:
    """Next level for one plot: the level above its current, iff prereqs are met."""
    spec = graph.spec(spec_id)
    if spec is None:
        return None
    nxt = spec.next_after(level_rank(levels.get(instance_id, 0)))
    if nxt is None:
        return None                                   # maxed
    for pre_id, pre_rank in nxt.prereqs:
        if graph.spec(pre_id) is None:
            continue                                  # unknown dep → don't hard-block
        if current_rank(levels, pre_id) < pre_rank:
            return None                               # locked: prereq not met
    return nxt


def _make_candidate(
    graph: BuildGraph, levels: Mapping[str, Any], resources: Mapping[str, Any] | None,
    spec_id: str, instance_id: str, track: str, value: float,
) -> BuildCandidate | None:
    nxt = _instance_next(graph, levels, spec_id, instance_id)
    if nxt is None:
        return None
    short = _shortfall(nxt.cost, resources) if resources is not None else ()
    return BuildCandidate(
        instance_id=instance_id, spec_id=spec_id, track=track,
        to_level=nxt.level, to_rank=nxt.rank, value=value,
        cost_total=sum(a for _, a in nxt.cost), affordable=not short,
        time_s=nxt.time_s, shortfall=short,
    )


def plan_builds(
    graph: BuildGraph,
    levels: Mapping[str, Any],
    *,
    role: RoleProfile | None = None,
    resources: Mapping[str, Any] | None = None,
    free_queues: int = 2,
    goal_id: str = DEFAULT_GOAL,
    goal_cap: float = DEFAULT_GOAL_CAP,
) -> BuildSlate:
    """Pick what to build across tracks to fill ``free_queues`` construction slots.

    Value-greedy + role-biased (the chosen policy): the furnace-first *progression*
    pick competes with *economy* (resource producers, the 8 Shelters, and the
    Storehouse) and *camp* candidates by value; the top affordable+ready ones fill
    the free queues. So a free queue never idles — when the furnace pick is
    unaffordable, the queue builds a producer / Shelter instead. ``role`` tilts
    economy↔battle; progression stays universal, and a role's ``no_build`` set is
    excluded outright (a farm drops the Storehouse to stay plunderable). Bottleneck
    repair (short resource → its producer) boosts that producer's value, but is
    inert until ``policy.PRODUCER_BY_ITEM`` is filled.
    """
    candidates: list[BuildCandidate] = []

    prog = plan_next(graph, levels, goal_id=goal_id, goal_cap=goal_cap, resources=resources)
    if prog.step is not None:
        c = _make_candidate(graph, levels, resources, prog.step.building_id,
                            prog.step.building_id, "progression", policy.PROGRESSION_WEIGHT)
        if c is not None:
            candidates.append(c)
        if not prog.affordable:                       # bottleneck repair (inert if unmapped)
            for item, _amt in prog.shortfall:
                producer = policy.PRODUCER_BY_ITEM.get(item)
                if not producer:
                    continue
                bc = _make_candidate(graph, levels, resources, producer, producer,
                                     "bottleneck", policy.BOTTLENECK_WEIGHT)
                if bc is not None:
                    candidates.append(bc)

    for spec_id in (*policy.PRODUCERS, policy.SHELTER_ID, *policy.PROTECTION):
        if graph.spec(spec_id) is None:
            continue
        value = policy.building_value(policy.economy_kind(spec_id), role)
        for inst in policy.instance_ids(spec_id):
            c = _make_candidate(graph, levels, resources, spec_id, inst, "economy", value)
            if c is not None:
                candidates.append(c)

    for spec_id in policy.CAMPS:
        if graph.spec(spec_id) is None:
            continue
        c = _make_candidate(graph, levels, resources, spec_id, spec_id, "camp",
                            policy.building_value("camp", role))
        if c is not None:
            candidates.append(c)

    # Role opt-outs: a farm never upgrades the Storehouse (keeps the pile raidable).
    # Drop blocked specs across every track before ranking so they can't fill a queue.
    if role is not None and role.no_build:
        candidates = [c for c in candidates if c.spec_id not in role.no_build]

    # Keep the highest-value candidate per plot (progression/bottleneck beat economy).
    best: dict[str, BuildCandidate] = {}
    for c in candidates:
        if c.instance_id not in best or c.value > best[c.instance_id].value:
            best[c.instance_id] = c
    ranked = tuple(sorted(best.values(), key=lambda c: (-c.value, c.cost_total, c.instance_id)))

    picks = tuple(c for c in ranked if c.affordable)[: max(0, free_queues)]
    if picks:
        reason = SELECTED
    elif ranked:
        reason = INSUFFICIENT_RESOURCES
    else:
        reason = ALL_MAXED
    return BuildSlate(picks=picks, candidates=ranked, reason=reason)
