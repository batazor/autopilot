"""Turn each domain planner's output into coordinator :class:`CandidateAction`s.

Thin, pure converters: they derive each action's shared ``cost`` (from the static
graphs) and its cross-domain ``priority`` (from :mod:`objective` + role). The
coordinator then arbitrates across domains. No IO.

Resource-key caveat: research costs are canonical names (meat/wood/coal/iron),
building costs are item-icon ids with no verified resource mapping yet — so
:func:`from_build_slate` only contributes shared cost for items present in the
``item_to_resource`` map (empty by default → building doesn't contend on the
shared pool until that map is filled; it still claims a construction channel).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .model import CONSTRUCTION, HERO, PET, RESEARCH, CandidateAction
from .objective import TRACK_DOMAIN, domain_priority

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.building.planner import BuildGraph, BuildSlate
    from games.wos.core.heroes.planner import HeroPlan
    from games.wos.core.pets.planner import PetPlan
    from games.wos.core.research.planner import ResearchGraph, ResearchPlan
    from games.wos.core.roles import RoleProfile


def from_build_slate(
    slate: BuildSlate,
    graph: BuildGraph,
    *,
    role: RoleProfile | None = None,
    item_to_resource: Mapping[str, str] | None = None,
    boosts: Mapping[str, float] | None = None,
    instance_boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """One construction candidate per pick in the build slate.

    ``boosts`` is the calendar bias (domain → multiplier) from
    :func:`events.calendar_bias`; ``instance_boosts`` is the economy bias's
    per-producer lift (building id → multiplier) from :func:`economy.economy_bias`
    (e.g. coal short → boost ``coal_mine``). Both absent → no bias.
    """
    out: list[CandidateAction] = []
    for i, pick in enumerate(slate.picks):
        domain = TRACK_DOMAIN.get(pick.track, "building_economy")
        spec = graph.spec(pick.spec_id)
        lvl = spec.level(pick.to_level) if spec else None
        cost: dict[str, int] = {}
        if lvl is not None and item_to_resource:
            for item, amount in lvl.cost:
                key = item_to_resource.get(item)
                if key:
                    cost[key] = cost.get(key, 0) + amount
        boost = (boosts or {}).get(domain, 1.0) * (instance_boosts or {}).get(pick.spec_id, 1.0)
        out.append(CandidateAction(
            domain=domain,
            channel_kind=CONSTRUCTION,
            key=pick.instance_id,
            priority=domain_priority(domain, role, rank_nudge=-float(i), boost=boost),
            cost=cost,
            detail=f"{pick.spec_id}->{pick.to_level}",
        ))
    return out


def from_research_plan(
    plan: ResearchPlan,
    graph: ResearchGraph,
    *,
    role: RoleProfile | None = None,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The single research candidate (one research queue), if any."""
    step = plan.step
    if step is None:
        return []
    node = graph.spec(step.node_id)
    cost: dict[str, int] = {}
    if node is not None:
        lv = node.level_at(step.to_level)
        if lv is not None:
            cost = dict(lv.cost)
    return [CandidateAction(
        domain="research",
        channel_kind=RESEARCH,
        key=step.node_id,
        priority=domain_priority("research", role, boost=(boosts or {}).get("research", 1.0)),
        cost=cost,
        detail=f"{step.node_id}->{step.to_level}",
    )]


def from_hero_plan(
    plan: HeroPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The hero-investment candidate (one hero channel), if any.

    Role is NOT re-applied here — the hero planner already baked role + generation
    into its pick's value; the coordinator just slots it at the heroes band. ``cost``
    is the tiered/per-hero books & shards (``book:<tier>`` / ``shard:<id>``).
    """
    step = plan.step
    if step is None:
        return []
    return [CandidateAction(
        domain="heroes",
        channel_kind=HERO,
        key=f"{step.hero_id}:{step.kind}",
        priority=domain_priority("heroes", boost=(boosts or {}).get("heroes", 1.0)),
        cost=dict(step.cost),
        detail=f"{step.hero_id} {step.kind} -> {step.to_level}",
    )]


def from_pet_plan(
    plan: PetPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The pet-investment candidate (one pet channel), if any. Role is baked into
    the pet planner's pick; cost is per-pet shards / shared pet food."""
    step = plan.step
    if step is None:
        return []
    return [CandidateAction(
        domain="pets",
        channel_kind=PET,
        key=f"{step.pet_id}:{step.kind}",
        priority=domain_priority("pets", boost=(boosts or {}).get("pets", 1.0)),
        cost=dict(step.cost),
        detail=f"{step.pet_id} {step.kind} -> {step.to_level}",
    )]
