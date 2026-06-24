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

from .model import (
    CHARM,
    CONSTRUCTION,
    GEAR,
    HERO,
    HERO_GEAR,
    MARCH,
    PET,
    RESEARCH,
    TRAINING,
    CandidateAction,
)
from .objective import TRACK_DOMAIN, domain_priority

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.building.planner import BuildGraph, BuildSlate
    from games.wos.core.charms.planner import CharmPlan
    from games.wos.core.gear.planner import GearPlan
    from games.wos.core.hero_gear.planner import HeroGearPlan
    from games.wos.core.pets.planner import PetPlan
    from games.wos.core.research.planner import ResearchGraph, ResearchPlan
    from games.wos.core.roles import RoleProfile
    from games.wos.heroes.heroes.planner import HeroPlan
    from games.wos.intel.planner import IntelPlan
    from games.wos.troops.planner import TrainingPlan


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


def from_charms_plan(
    plan: CharmPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The Chief Charm pick (one charm channel), if any.

    Role is baked into the charm planner's value; the coordinator slots it at the
    ``charms`` band. ``cost`` is the shared charm-material pool (guide/design/secrets).
    """
    step = plan.step
    if step is None:
        return []
    return [CandidateAction(
        domain="charms",
        channel_kind=CHARM,
        key=f"{step.slot_id}:L{step.to_level}",
        priority=domain_priority("charms", boost=(boosts or {}).get("charms", 1.0)),
        cost=dict(step.cost),
        detail=f"charm {step.slot_id} -> L{step.to_level}",
    )]


def from_gear_plan(
    plan: GearPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The Chief Gear pick (one gear channel), if any.

    Role is baked into the gear planner's value; the coordinator slots it at the
    ``gear`` band. ``cost`` is the shared gear-material pool (alloy/polishing/design/
    amber).
    """
    step = plan.step
    if step is None:
        return []
    return [CandidateAction(
        domain="gear",
        channel_kind=GEAR,
        key=f"{step.slot_id}:{step.label}",
        priority=domain_priority("gear", boost=(boosts or {}).get("gear", 1.0)),
        cost=dict(step.cost),
        detail=f"gear {step.slot_id} -> {step.label}",
    )]


def from_hero_gear_plan(
    plan: HeroGearPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The Hero Gear pick (one hero-gear channel), if any.

    Role is baked into the planner's value; the coordinator slots it at the
    ``hero_gear`` band. ``cost`` is the track's single material (enhance/mastery/widget).
    """
    step = plan.step
    if step is None:
        return []
    return [CandidateAction(
        domain="hero_gear",
        channel_kind=HERO_GEAR,
        key=f"{step.slot_id}:{step.track}:{step.to_level}",
        priority=domain_priority("hero_gear", boost=(boosts or {}).get("hero_gear", 1.0)),
        cost=dict(step.cost),
        detail=f"hero gear {step.slot_id} {step.track} -> {step.to_level}",
    )]


def from_training_plan(
    plan: TrainingPlan,
    *,
    role: RoleProfile | None = None,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The troop-training candidate (one camp pick) for the TRAINING channel.

    The planner already chose the type (army-composition deficit) and tier; the
    coordinator slots it at the ``troops`` band (battle category → fighters lift it,
    farms drop it) and any live training/mobilization event boosts it. ``cost`` is the
    batch's meat/wood/coal/iron from the training table — so training contends on the
    shared resource pool (empty until that table is filled → contends on priority).
    """
    step = plan.step
    if step is None:
        return []
    return [CandidateAction(
        domain="troops",
        channel_kind=TRAINING,
        key=f"{step.troop_type}:t{step.tier}",
        priority=domain_priority("troops", role, boost=(boosts or {}).get("troops", 1.0)),
        cost=dict(step.cost),
        detail=f"train {step.troop_type} T{step.tier} ({step.name})",
    )]


def from_intel_plan(
    plan: IntelPlan,
    *,
    role: RoleProfile | None = None,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """One MARCH candidate per marker the Intel planner queued this pass.

    Intel events deploy a (short) march and cost the shared stamina pool, so each
    candidate contends on the MARCH channel with cost ``{"stamina": n}``. The intel
    band sits above gather/raids (see :mod:`objective`) so a quick, expiring Intel
    run is taken before a long gather. ``rank_nudge`` preserves the planner's
    value order across the available march slots; stamina balance + the daily quota
    are already applied inside the planner's batch.
    """
    boost = (boosts or {}).get("intel", 1.0)
    out: list[CandidateAction] = []
    for i, cand in enumerate(plan.batch):
        ev = cand.event
        out.append(CandidateAction(
            domain="intel",
            channel_kind=MARCH,
            key=f"intel:{ev.color}:{ev.kind}:{ev.x},{ev.y}",
            priority=domain_priority("intel", role, rank_nudge=-float(i), boost=boost),
            cost={"stamina": int(cand.cost)},
            detail=f"intel {ev.color} {ev.kind}",
        ))
    return out
