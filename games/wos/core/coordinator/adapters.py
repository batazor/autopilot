"""Turn each domain planner's output into coordinator :class:`CandidateAction`s.

Thin, pure converters: each domain now emits its **full ranked set of alternatives**
(not just the pre-chosen top pick), so the coordinator's optimizer can actually
arbitrate among them under the shared budget — a cheaper sibling can win a lane when
the top is starved, and a domain can yield a contended resource by taking a slightly
lower alternative. Each candidate carries a :class:`~model.Utility` breakdown whose
``base_value`` is the cross-domain band (× role × event boost) plus a small
``intra``-domain term derived from the planner's own per-candidate value, so the
alternatives keep their value order within the band. No IO.

The intra term is deliberately *small* (``_INTRA_SPAN``) this iteration: it orders a
domain's alternatives without letting one domain's value cross another's band — the
conservative first landing. Widening it so value can override bands is the
calibration deferred to the coordinator plan's later steps (shadow prices / MPC).

Resource-key caveat: research costs are canonical names (meat/wood/coal/iron),
building costs are item-icon ids with no verified resource mapping yet — so
:func:`from_build_slate` only contributes shared cost for items present in the
``item_to_resource`` map (empty by default → building doesn't contend on the
shared pool until that map is filled; it still claims a construction channel).

Building exception: ``plan_builds`` is authoritative on *which* buildings to build
(it cross-ranks tracks by value: furnace-first, bottleneck repair). So
:func:`from_build_slate` keeps the planner's ``picks`` at full priority and emits the
remaining ranked candidates as **fallbacks below the picks** — they only fill a
construction lane when a pick is starved, never override the planner's selection.
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
    VIP,
    CandidateAction,
    Utility,
)
from .objective import TRACK_DOMAIN, domain_priority

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from games.wos.core.building.planner import BuildCandidate, BuildGraph, BuildSlate
    from games.wos.core.charms.planner import CharmPlan
    from games.wos.core.gear.planner import GearPlan
    from games.wos.core.hero_gear.planner import HeroGearPlan
    from games.wos.core.pets.planner import PetPlan
    from games.wos.core.research.planner import ResearchCandidate, ResearchGraph, ResearchPlan
    from games.wos.core.roles import RoleProfile
    from games.wos.core.vip.planner import VipPlan
    from games.wos.heroes.heroes.planner import HeroPlan
    from games.wos.intel.planner import IntelPlan
    from games.wos.troops.planner import TrainingPlan

# How far a domain's *worst* alternative sits below its best within the band. The
# top candidate keeps exactly ``band × boost`` (intra 0) — today's single-pick value
# — and lower alternatives are nudged below it. Kept under the tightest cross-band
# gap on any shared channel kind (intel 760 vs romance 750 on MARCH) so alternatives
# order *within* a domain without reordering domains. Widening this (so a high-value
# action can cross a band) is calibration — see the plan's shadow-price / MPC steps.
_INTRA_SPAN = 5.0

# Building non-pick candidates are emitted this far below the picks so they act as
# starvation fallbacks on the construction lane without ever outranking a pick.
_EXTRA_FALLBACK_OFFSET = 1000.0


def _intra(values: Sequence[float], span: float = _INTRA_SPAN) -> list[float]:
    """Per-candidate value → an intra-domain offset in ``[-span, 0]`` (best → 0).

    Each alternative is nudged below the best in proportion to how much *less*
    valuable it is, as a fraction of the best's value: ``-span × (best − v) / best``.
    So near-equal alternatives stay near the top while a much weaker one drops toward
    ``-span`` — the magnitude of the value gap is carried, not just the order. The best
    candidate keeps ``base_value == band × boost`` (intra 0, = the pre-alternatives
    single-pick value); a single candidate or all-zero values map to all-zeros (no
    behaviour change for one-candidate domains).
    """
    if not values:
        return []
    vmax = max(values)
    if vmax <= 0:
        return [0.0] * len(values)
    return [-span * min(1.0, max(0.0, (vmax - v) / vmax)) for v in values]


def _emit(
    cands: Sequence[object],
    value_of: Callable[[object], float],
    make: Callable[[object, float], CandidateAction],
) -> list[CandidateAction]:
    """Build one :class:`CandidateAction` per candidate, value-ordered via ``intra``."""
    intra = _intra([float(value_of(c)) for c in cands])
    return [make(c, iv) for c, iv in zip(cands, intra, strict=True)]


def from_build_slate(
    slate: BuildSlate,
    graph: BuildGraph,
    *,
    role: RoleProfile | None = None,
    item_to_resource: Mapping[str, str] | None = None,
    boosts: Mapping[str, float] | None = None,
    instance_boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """One construction candidate per pick, plus the rest of the slate as fallbacks.

    The planner's ``picks`` keep their priority (``plan_builds`` already chose them by
    value); every other ranked candidate is emitted ``_EXTRA_FALLBACK_OFFSET`` below
    the picks so it only fills a construction lane when a pick is starved.

    ``boosts`` is the calendar bias (domain → multiplier) from
    :func:`events.calendar_bias`; ``instance_boosts`` is the economy bias's
    per-producer lift (building id → multiplier) from :func:`economy.economy_bias`
    (e.g. coal short → boost ``coal_mine``). Both absent → no bias.
    """
    pick_ids = {p.instance_id for p in slate.picks}
    extras = tuple(c for c in slate.candidates if c.instance_id not in pick_ids)

    def _cost(pick: BuildCandidate) -> dict[str, int]:
        spec = graph.spec(pick.spec_id)
        lvl = spec.level(pick.to_level) if spec else None
        cost: dict[str, int] = {}
        if lvl is not None and item_to_resource:
            for item, amount in lvl.cost:
                key = item_to_resource.get(item)
                if key:
                    cost[key] = cost.get(key, 0) + amount
        return cost

    def _action(pick: BuildCandidate, nudge: float) -> CandidateAction:
        domain = TRACK_DOMAIN.get(pick.track, "building_economy")
        boost = (boosts or {}).get(domain, 1.0) * (instance_boosts or {}).get(pick.spec_id, 1.0)
        return CandidateAction(
            domain=domain,
            channel_kind=CONSTRUCTION,
            key=pick.instance_id,
            utility=Utility(
                base_value=domain_priority(domain, role, rank_nudge=nudge, boost=boost),
                event_points=float(getattr(pick, "event_points", 0) or 0),
                time_cost=float(getattr(pick, "time_s", 0) or 0),
            ),
            cost=_cost(pick),
            detail=f"{pick.spec_id}->{pick.to_level}",
        )

    out = _emit(slate.picks, lambda c: c.value, _action)
    out += _emit(extras, lambda c: c.value, lambda c, iv: _action(c, iv - _EXTRA_FALLBACK_OFFSET))
    return out


def from_research_plan(
    plan: ResearchPlan,
    graph: ResearchGraph,
    *,
    role: RoleProfile | None = None,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """Every researchable-now tech the planner ranked (one research queue picks one).

    ``ResearchCandidate`` carries no cost, so each level's resource cost is re-derived
    from the graph (exactly as the single-step adapter did). Research already folds an
    unlocked tech's downstream value into its priority (``effective_priorities``), so
    that value rides in ``base_value`` — no separate ``unlock_value`` (would double-count).
    """
    boost = (boosts or {}).get("research", 1.0)

    def _make(c: ResearchCandidate, iv: float) -> CandidateAction:
        node = graph.spec(c.node_id)
        cost: dict[str, int] = {}
        if node is not None:
            lv = node.level_at(c.to_level)
            if lv is not None:
                cost = dict(lv.cost)
        return CandidateAction(
            domain="research",
            channel_kind=RESEARCH,
            key=c.node_id,
            utility=Utility(base_value=domain_priority("research", role, rank_nudge=iv, boost=boost)),
            cost=cost,
            detail=f"{c.node_id}->{c.to_level}",
        )

    return _emit(plan.candidates, lambda c: c.priority, _make)


def from_hero_plan(
    plan: HeroPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """Every hero-investment alternative (one hero channel picks one).

    Role is NOT re-applied here — the hero planner already baked role + generation
    into each candidate's value; the coordinator just slots them at the heroes band.
    ``cost`` is the tiered/per-hero books & shards (``book:<tier>`` / ``shard:<id>``).
    """
    boost = (boosts or {}).get("heroes", 1.0)
    return _emit(plan.candidates, lambda c: c.value, lambda c, iv: CandidateAction(
        domain="heroes",
        channel_kind=HERO,
        key=f"{c.hero_id}:{c.kind}",
        utility=Utility(base_value=domain_priority("heroes", rank_nudge=iv, boost=boost)),
        cost=dict(c.cost),
        detail=f"{c.hero_id} {c.kind} -> {c.to_level}",
    ))


def from_pet_plan(
    plan: PetPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """Every pet-investment alternative (one pet channel picks one). Role is baked
    into the pet planner's values; cost is per-pet shards / shared pet food."""
    boost = (boosts or {}).get("pets", 1.0)
    return _emit(plan.candidates, lambda c: c.value, lambda c, iv: CandidateAction(
        domain="pets",
        channel_kind=PET,
        key=f"{c.pet_id}:{c.kind}",
        utility=Utility(base_value=domain_priority("pets", rank_nudge=iv, boost=boost)),
        cost=dict(c.cost),
        detail=f"{c.pet_id} {c.kind} -> {c.to_level}",
    ))


def from_charms_plan(
    plan: CharmPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """Every Chief Charm alternative (one charm channel picks one).

    Role is baked into the charm planner's values; the coordinator slots them at the
    ``charms`` band. ``cost`` is the shared charm-material pool (guide/design/secrets).
    """
    boost = (boosts or {}).get("charms", 1.0)
    return _emit(plan.candidates, lambda c: c.value, lambda c, iv: CandidateAction(
        domain="charms",
        channel_kind=CHARM,
        key=f"{c.slot_id}:L{c.to_level}",
        utility=Utility(base_value=domain_priority("charms", rank_nudge=iv, boost=boost)),
        cost=dict(c.cost),
        detail=f"charm {c.slot_id} -> L{c.to_level}",
    ))


def from_gear_plan(
    plan: GearPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """Every Chief Gear alternative (one gear channel picks one).

    Role is baked into the gear planner's values; the coordinator slots them at the
    ``gear`` band. ``cost`` is the shared gear-material pool (alloy/polishing/design/
    amber).
    """
    boost = (boosts or {}).get("gear", 1.0)
    return _emit(plan.candidates, lambda c: c.value, lambda c, iv: CandidateAction(
        domain="gear",
        channel_kind=GEAR,
        key=f"{c.slot_id}:{c.label}",
        utility=Utility(base_value=domain_priority("gear", rank_nudge=iv, boost=boost)),
        cost=dict(c.cost),
        detail=f"gear {c.slot_id} -> {c.label}",
    ))


def from_hero_gear_plan(
    plan: HeroGearPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """Every Hero Gear alternative (one hero-gear channel picks one).

    Role is baked into the planner's values; the coordinator slots them at the
    ``hero_gear`` band. ``cost`` is the track's single material (enhance/mastery/widget).
    """
    boost = (boosts or {}).get("hero_gear", 1.0)
    return _emit(plan.candidates, lambda c: c.value, lambda c, iv: CandidateAction(
        domain="hero_gear",
        channel_kind=HERO_GEAR,
        key=f"{c.slot_id}:{c.track}:{c.to_level}",
        utility=Utility(base_value=domain_priority("hero_gear", rank_nudge=iv, boost=boost)),
        cost=dict(c.cost),
        detail=f"hero gear {c.slot_id} {c.track} -> {c.to_level}",
    ))


def from_vip_plan(
    plan: VipPlan,
    *,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """The VIP level-up candidate(s) — a single linear track, so at most one.

    The coordinator slots it at the ``vip`` band; ``cost`` is the remaining
    ``vip_points`` to that level (VIP Points apply 1:1 as VIP XP). Empty when maxed.
    """
    boost = (boosts or {}).get("vip", 1.0)
    return _emit(plan.candidates, lambda c: c.value, lambda c, iv: CandidateAction(
        domain="vip",
        channel_kind=VIP,
        key=f"L{c.to_level}",
        utility=Utility(base_value=domain_priority("vip", rank_nudge=iv, boost=boost)),
        cost=dict(c.cost),
        detail=f"vip -> L{c.to_level}",
    ))


def from_training_plan(
    plan: TrainingPlan,
    *,
    role: RoleProfile | None = None,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """Every troop-training alternative the planner offered (one TRAINING channel).

    The planner ranks types by army-composition deficit and may add a ``promote``
    sibling; the coordinator slots them at the ``troops`` band (battle category →
    fighters lift it, farms drop it) and any live training/mobilization event boosts
    it. ``cost`` is the batch's meat/wood/coal/iron (empty until the table is filled →
    contends on priority). ``kind`` is in the key so train + promote don't collide.
    """
    boost = (boosts or {}).get("troops", 1.0)
    return _emit(plan.candidates, lambda c: c.power, lambda c, iv: CandidateAction(
        domain="troops",
        channel_kind=TRAINING,
        key=f"{c.troop_type}:t{c.tier}:{c.kind}",
        utility=Utility(
            base_value=domain_priority("troops", role, rank_nudge=iv, boost=boost),
            time_cost=float(getattr(c, "time_s", 0) or 0),
        ),
        cost=dict(c.cost),
        detail=f"{c.kind} {c.troop_type} T{c.tier} ({c.name})",
    ))


def from_intel_plan(
    plan: IntelPlan,
    *,
    role: RoleProfile | None = None,
    boosts: Mapping[str, float] | None = None,
) -> list[CandidateAction]:
    """One MARCH candidate per marker the Intel planner queued this pass.

    Intel events deploy a (short) march and cost the shared stamina pool, so each
    candidate contends on the MARCH channel with cost ``{"stamina": n}``. The intel
    band sits above gather/raids (see :mod:`objective`) so a quick, expiring Intel run
    is taken before a long gather; the ``intra`` term keeps the planner's value order
    across the available march slots. Stamina balance + the daily quota are already
    applied inside the planner's batch.
    """
    boost = (boosts or {}).get("intel", 1.0)
    return _emit(plan.batch, lambda c: c.value, lambda c, iv: CandidateAction(
        domain="intel",
        channel_kind=MARCH,
        key=f"intel:{c.event.color}:{c.event.kind}:{c.event.x},{c.event.y}",
        utility=Utility(base_value=domain_priority("intel", role, rank_nudge=iv, boost=boost)),
        cost={"stamina": int(c.cost)},
        detail=f"intel {c.event.color} {c.event.kind}",
    ))
