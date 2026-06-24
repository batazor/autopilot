"""Planner calculators — interactive what-if endpoints over the per-domain planners.

The live state readers are deferred, so these endpoints take the player state as
request *input* (building levels / owned heroes / resources / server age …) and
return the planner's recommendation. Pure compute — no Redis, no device, no
mutation — which makes them safe to expose as an operator "calculator" page.

Each domain maps to one planner entry point:
- building     -> games.wos.core.building.planner.plan_builds
- research     -> games.wos.core.research.planner.plan_next
- heroes       -> games.wos.heroes.heroes.planner.plan_next
- pets         -> games.wos.core.pets.planner.plan_next
- intel        -> games.wos.intel.planner.plan_next
- coordinator  -> games.wos.core.coordinator.coordinate

Results are returned as the planner dataclasses serialised via ``dataclasses.asdict``
(tuples become JSON arrays, nested dataclasses/Mappings serialise recursively).
"""
from __future__ import annotations

import dataclasses
from functools import lru_cache
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from collections.abc import Callable

router = APIRouter(prefix="/api/planner", tags=["planner"])


# --------------------------------------------------------------------------- #
# Cached static-graph / catalog loaders (read YAML once per process)
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def _building_graph() -> Any:
    from games.wos.core.building.planner import load_graph

    return load_graph()


@lru_cache(maxsize=1)
def _research_graph() -> Any:
    from games.wos.core.research.planner import load_research_graph

    return load_research_graph()


@lru_cache(maxsize=1)
def _hero_catalog() -> Any:
    from games.wos.heroes.heroes.planner import load_hero_catalog

    return load_hero_catalog()


@lru_cache(maxsize=1)
def _pet_catalog() -> Any:
    from games.wos.core.pets.planner import load_pet_catalog

    return load_pet_catalog()


@lru_cache(maxsize=4)
def _unlock_schedule(profile: str) -> Any:
    from games.wos.core.calendar.server_unlocks import load_unlock_schedule

    return load_unlock_schedule(profile)


def _role(role_id: str | None) -> Any:
    from games.wos.core.roles import get_role

    return get_role(role_id) if role_id else None


def _asdict(obj: Any) -> Any:
    return dataclasses.asdict(obj)


def _guard(fn: Callable[[], Any]) -> Any:
    """Run a planner call, turning value errors into 400s and the rest into 500s."""
    try:
        return fn()
    except HTTPException:
        raise
    except (KeyError, ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad input: {exc}") from exc
    except Exception as exc:  # surface planner failures to the operator
        raise HTTPException(status_code=500, detail=f"planner error: {exc}") from exc


# --------------------------------------------------------------------------- #
# Meta — option lists that power the form dropdowns / placeholders
# --------------------------------------------------------------------------- #
@router.get("/meta")
def get_meta() -> dict[str, Any]:
    from games.wos.core.coordinator import (
        CONSTRUCTION,
        HERO,
        MARCH,
        PET,
        RESEARCH,
        TRAINING,
    )
    from games.wos.core.roles import ROLES

    def _safe(fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception:  # best-effort option lists; never fail the meta call
            return default

    return {
        "roles": list(ROLES.keys()),
        "buildings": _safe(lambda: sorted(_building_graph().buildings), []),
        "heroes": _safe(lambda: sorted(_hero_catalog().keys()), []),
        "pets": _safe(lambda: sorted(_pet_catalog().keys()), []),
        "channel_kinds": [CONSTRUCTION, RESEARCH, MARCH, TRAINING, HERO, PET],
        "intel_kinds": _safe(_intel_kinds, []),
        "intel_colors": _safe(_intel_colors, []),
    }


def _intel_kinds() -> list[str]:
    from games.wos.intel.planner import MARKER_KIND_WEIGHT

    return sorted(MARKER_KIND_WEIGHT.keys())


def _intel_colors() -> list[str]:
    from games.wos.intel.planner import MARKER_COLOR_WEIGHT

    return sorted(MARKER_COLOR_WEIGHT.keys())


# --------------------------------------------------------------------------- #
# Building
# --------------------------------------------------------------------------- #
class BuildingBody(BaseModel):
    levels: dict[str, Any] = Field(default_factory=dict)
    resources: dict[str, int] | None = None
    role: str | None = None
    goal_id: str = "furnace"
    goal_cap: float = 30.0
    free_queues: int = Field(default=2, ge=1, le=10)
    # Live points-event slugs (svs / state_of_power / hall_of_chief / power_up …).
    # When set, the slate scores each upgrade's event points and tilts ranking toward
    # higher-power upgrades for the window.
    active_events: list[str] = Field(default_factory=list)


@router.post("/building")
def post_building(body: BuildingBody) -> dict[str, Any]:
    from games.wos.core.building.planner import plan_builds

    slate = _guard(
        lambda: plan_builds(
            _building_graph(),
            body.levels,
            role=_role(body.role),
            resources=body.resources,
            free_queues=body.free_queues,
            goal_id=body.goal_id,
            goal_cap=body.goal_cap,
            active_events=body.active_events,
        )
    )
    return _asdict(slate)


@router.get("/building/catalog")
def get_building_catalog() -> dict[str, Any]:
    """Every building + its level ladder, for the levels editor dropdowns."""
    graph = _building_graph()
    buildings = [
        {
            "id": spec.id,
            "name": spec.name,
            "levels": [lvl.level for lvl in spec.levels],
            "max_level": spec.levels[-1].level if spec.levels else None,
        }
        for spec in sorted(graph.buildings.values(), key=lambda s: s.name)
    ]
    return {"buildings": buildings}


class AutofillBody(BaseModel):
    levels: dict[str, Any] = Field(default_factory=dict)


@router.post("/building/autofill")
def post_building_autofill(body: AutofillBody) -> dict[str, Any]:
    """Backfill prerequisite levels implied by the given levels.

    Setting Furnace 30 implies every building on its prerequisite closure must
    exist at its required level (Embassy, camps, …). We walk each set building's
    per-level ``prerequisites`` up to its rank, raising every dependency to its
    required rank to a fixpoint, then map ranks back to level keys. Buildings the
    operator set higher than implied are kept (the seed is the max of input).
    """
    from games.wos.core.building.planner import level_rank

    graph = _building_graph()

    # Seed required ranks from the operator's input (max wins on duplicates).
    req: dict[str, float] = {}
    for bid, val in body.levels.items():
        if graph.spec(bid) is None:
            continue
        req[bid] = max(req.get(bid, 0.0), level_rank(val))

    # Fixpoint: a building at rank R requires every prereq of its levels ≤ R.
    changed = True
    while changed:
        changed = False
        for bid, rank in list(req.items()):
            spec = graph.spec(bid)
            if spec is None:
                continue
            for lvl in spec.levels:  # sorted ascending by rank
                if lvl.rank > rank:
                    break
                for pre_id, pre_rank in lvl.prereqs:
                    if graph.spec(pre_id) is None:
                        continue
                    if pre_rank > req.get(pre_id, 0.0):
                        req[pre_id] = pre_rank
                        changed = True

    # Map each required rank back to a concrete level key.
    out: dict[str, str] = {}
    for bid, rank in req.items():
        spec = graph.spec(bid)
        if spec is None or not spec.levels:
            continue
        exact = next((lvl.level for lvl in spec.levels if lvl.rank == rank), None)
        at_least = next((lvl.level for lvl in spec.levels if lvl.rank >= rank), None)
        out[bid] = exact or at_least or spec.levels[-1].level

    added = sorted(k for k in out if k not in body.levels)
    return {"levels": out, "added": added}


# --------------------------------------------------------------------------- #
# Research
# --------------------------------------------------------------------------- #
class ResearchBody(BaseModel):
    levels: dict[str, int] = Field(default_factory=dict)
    rc_level: int = Field(default=10, ge=1)
    role: str | None = None


@router.post("/research")
def post_research(body: ResearchBody) -> dict[str, Any]:
    from games.wos.core.research.planner import plan_next

    plan = _guard(
        lambda: plan_next(
            _research_graph(),
            body.levels,
            body.rc_level,
            role=_role(body.role),
        )
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Heroes
# --------------------------------------------------------------------------- #
class HeroesBody(BaseModel):
    owned: dict[str, dict[str, int]] = Field(default_factory=dict)
    resources: dict[str, int] = Field(default_factory=dict)
    current_generation: int | None = None
    # If current_generation is omitted, derive it from the server age via the
    # unlock schedule profile (beta by default) — the Hero Hall opens generations
    # by server age, so the planner's gen gate follows the calendar automatically.
    server_age_days: int | None = None
    server_profile: str = "beta"
    role: str | None = None


def _hero_current_generation(body: HeroesBody) -> int | None:
    """Explicit ``current_generation`` wins; else derive it from the server age."""
    if body.current_generation is not None:
        return body.current_generation
    if body.server_age_days is not None:
        return _unlock_schedule(body.server_profile).hero_generation_at(body.server_age_days)
    return None


@router.post("/heroes")
def post_heroes(body: HeroesBody) -> dict[str, Any]:
    from games.wos.heroes.heroes.planner import plan_next

    plan = _guard(
        lambda: plan_next(
            _hero_catalog(),
            body.owned,
            body.resources,
            current_generation=_hero_current_generation(body),
            role=_role(body.role),
        )
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Pets
# --------------------------------------------------------------------------- #
class PetsBody(BaseModel):
    owned: dict[str, dict[str, int]] = Field(default_factory=dict)
    resources: dict[str, int] = Field(default_factory=dict)
    server_days: int | None = None
    role: str | None = None


@router.post("/pets")
def post_pets(body: PetsBody) -> dict[str, Any]:
    from games.wos.core.pets.planner import plan_next

    plan = _guard(
        lambda: plan_next(
            _pet_catalog(),
            body.owned,
            body.resources,
            server_days=body.server_days,
            role=_role(body.role),
        )
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Troop training
# --------------------------------------------------------------------------- #
class TroopsBody(BaseModel):
    counts: dict[str, int] | None = None          # per-type pool (from the reader)
    max_tier: dict[str, int] | int = 11            # camp/research-gated tier cap
    fc: dict[str, int] | int = 0                   # Fire-Crystal level per type
    target: dict[str, float] | None = None         # army-composition target shares


@router.post("/training")
def post_training(body: TroopsBody) -> dict[str, Any]:
    from games.wos.troops.planner import plan_next

    plan = _guard(
        lambda: plan_next(
            counts=body.counts,
            max_tier=body.max_tier,
            fc=body.fc,
            target=body.target,
        )
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Intel
# --------------------------------------------------------------------------- #
class IntelEventBody(BaseModel):
    kind: str
    color: str
    score: float = 1.0
    x: int = 0
    y: int = 0


class IntelBody(BaseModel):
    events: list[IntelEventBody] = Field(default_factory=list)
    stamina: float | None = None
    cost_per_event: int = 10
    reserve: int = 0
    daily_quota_left: int | None = None
    min_value: float = 0.0
    priority_only: bool = False


@router.post("/intel")
def post_intel(body: IntelBody) -> dict[str, Any]:
    from games.wos.intel.planner import IntelEvent, plan_next

    events = [
        IntelEvent(kind=e.kind, color=e.color, score=e.score, x=e.x, y=e.y)
        for e in body.events
    ]
    plan = _guard(
        lambda: plan_next(
            events,
            stamina=body.stamina,
            cost_per_event=body.cost_per_event,
            reserve=body.reserve,
            daily_quota_left=body.daily_quota_left,
            min_value=body.min_value,
            priority_only=body.priority_only,
        )
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Coordinator
# --------------------------------------------------------------------------- #
class ChannelBody(BaseModel):
    id: str
    kind: str


class CandidateBody(BaseModel):
    domain: str
    channel_kind: str
    key: str
    priority: float
    cost: dict[str, int] = Field(default_factory=dict)
    detail: str = ""


class CoordinatorBody(BaseModel):
    channels: list[ChannelBody] = Field(default_factory=list)
    candidates: list[CandidateBody] = Field(default_factory=list)
    balances: dict[str, int] = Field(default_factory=dict)
    # True → exact value-maximising allocation (branch-and-bound, ≥ greedy);
    # False → the plain greedy fill. Defaults to optimal.
    optimize: bool = True


@router.post("/coordinator")
def post_coordinator(body: CoordinatorBody) -> dict[str, Any]:
    from games.wos.core.coordinator import (
        CandidateAction,
        Channel,
        coordinate,
        coordinate_optimal,
    )

    channels = [Channel(id=c.id, kind=c.kind) for c in body.channels]
    candidates = [
        CandidateAction(
            domain=c.domain,
            channel_kind=c.channel_kind,
            key=c.key,
            priority=c.priority,
            cost=dict(c.cost),
            detail=c.detail,
        )
        for c in body.candidates
    ]
    solver = coordinate_optimal if body.optimize else coordinate
    decision = _guard(lambda: solver(channels, candidates, body.balances))
    return _asdict(decision)


# --------------------------------------------------------------------------- #
# Safety — defensive directive from the current threat state
# --------------------------------------------------------------------------- #
class SafetyBody(BaseModel):
    incoming_attack: bool = False
    attack_eta_s: float = 0.0
    shield_active: bool = False
    shield_remaining_s: float = 0.0
    pvp_window: bool = False
    troops_exposed: bool = False
    gatherers_under_attack: bool = False
    injured: int = 0


@router.post("/safety")
def post_safety(body: SafetyBody) -> dict[str, Any]:
    from games.wos.core.coordinator import ThreatState, assess_safety

    threat = ThreatState(**body.model_dump())
    directive = _guard(lambda: assess_safety(threat))
    return _asdict(directive)


# --------------------------------------------------------------------------- #
# Chief Orders — order the chief orders by fit to the situation
# --------------------------------------------------------------------------- #
class ChiefOrdersBody(BaseModel):
    active_categories: list[str] = Field(default_factory=list)
    injured: int = 0
    pvp_window: bool = False


@router.post("/chief_orders")
def post_chief_orders(body: ChiefOrdersBody) -> dict[str, Any]:
    from games.wos.core.coordinator import recommend_orders

    plan = _guard(
        lambda: recommend_orders(
            active_categories=body.active_categories,
            injured=body.injured,
            pvp_window=body.pvp_window,
        )
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Speedups — apply speedup inventory to the longest running tasks
# --------------------------------------------------------------------------- #
class SpeedupTaskBody(BaseModel):
    id: str
    category: str
    remaining_s: float = 0.0


class SpeedupsBody(BaseModel):
    tasks: list[SpeedupTaskBody] = Field(default_factory=list)
    inventory_minutes: dict[str, int] = Field(default_factory=dict)
    spend_now: bool = True


@router.post("/speedups")
def post_speedups(body: SpeedupsBody) -> dict[str, Any]:
    from games.wos.core.coordinator import recommend_speedups
    from games.wos.core.coordinator.premium import SpeedupTask

    tasks = [
        SpeedupTask(id=t.id, category=t.category, remaining_s=t.remaining_s)
        for t in body.tasks
    ]
    plan = _guard(
        lambda: recommend_speedups(
            tasks, body.inventory_minutes, spend_now=body.spend_now
        )
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Currency — spend a premium-currency balance on the best-ROI sinks
# --------------------------------------------------------------------------- #
class CurrencySinkBody(BaseModel):
    id: str
    currency: str
    cost: int
    value: float
    available: bool = True


class CurrencyBody(BaseModel):
    balance: int = 0
    currency: str = "diamonds"
    sinks: list[CurrencySinkBody] = Field(default_factory=list)


@router.post("/currency")
def post_currency(body: CurrencyBody) -> dict[str, Any]:
    from games.wos.core.coordinator import allocate_currency
    from games.wos.core.coordinator.premium import CurrencySink

    sinks = [
        CurrencySink(
            id=s.id,
            currency=s.currency,
            cost=s.cost,
            value=s.value,
            available=s.available,
        )
        for s in body.sinks
    ]
    plan = _guard(
        lambda: allocate_currency(body.balance, sinks, currency=body.currency)
    )
    return _asdict(plan)


# --------------------------------------------------------------------------- #
# Per-player planner state — load/save the operator's annotated inputs from the
# canonical per-player store (db/state/state.db → gamers.state_json → .planner).
# Building/research levels are overlaid from the native (reader-owned) fields so
# live truth wins and the manual layer only fills the gaps.
# --------------------------------------------------------------------------- #
_PLANNER_STATE_DOMAINS = (
    "building",
    "research",
    "heroes",
    "pets",
    "intel",
    "coordinator",
    "safety",
    "chief_orders",
    "speedups",
    "currency",
)


def _player_store(player_id: str) -> Any:
    from config.state_store import get_state_store

    store = get_state_store().get(str(player_id))
    if store is None:
        raise HTTPException(status_code=404, detail=f"unknown player: {player_id}")
    return store


def _planner_domains_from_snapshot(snap: Any) -> dict[str, Any]:
    """Per-domain planner input bodies for one player: the saved manual layer
    with reader-owned building/research levels overlaid on top."""
    saved = dict(getattr(snap, "planner", {}) or {})
    native_buildings = dict(snap.buildings.levels or {})
    native_research = dict(snap.researches.levels or {})

    domains: dict[str, Any] = {
        d: dict(saved.get(d) or {}) for d in _PLANNER_STATE_DOMAINS
    }

    # Building: overlay reader-owned levels on top of the manual layer.
    b = domains["building"]
    b["levels"] = {**dict(b.get("levels") or {}), **native_buildings}

    # Research: overlay native levels; default RC level from the building data.
    r = domains["research"]
    r["levels"] = {**dict(r.get("levels") or {}), **native_research}
    if "rc_level" not in r and native_buildings.get("research_center"):
        r["rc_level"] = int(native_buildings["research_center"])

    return domains


@router.get("/state/{player_id}")
def get_player_planner_state(player_id: str) -> dict[str, Any]:
    snap = _player_store(player_id).snapshot()
    return {
        "player_id": str(player_id),
        "nickname": snap.nickname,
        "domains": _planner_domains_from_snapshot(snap),
    }


class PlannerStateBody(BaseModel):
    domains: dict[str, Any] = Field(default_factory=dict)


@router.put("/state/{player_id}")
def put_player_planner_state(
    player_id: str, body: PlannerStateBody
) -> dict[str, Any]:
    store = _player_store(player_id)
    clean = {
        d: body.domains[d]
        for d in _PLANNER_STATE_DOMAINS
        if isinstance(body.domains.get(d), dict)
    }
    store.set("planner", clean)
    return {"ok": True, "player_id": str(player_id), "saved_domains": sorted(clean)}


# --------------------------------------------------------------------------- #
# Full plan — run every domain planner and let the coordinator arbitrate the
# shared resource pool across the parallel execution channels. The unified
# "what should this account do next" answer that the brain exists to produce.
# --------------------------------------------------------------------------- #
class EventWindowBody(BaseModel):
    slug: str
    active: bool = False
    starts_in_s: float = 0.0
    ends_in_s: float = 0.0
    phase_category: str | None = None


class DailyTaskBody(BaseModel):
    id: str
    category: str
    target: int = 1
    progress: int = 0
    claimable: bool = False


class FullPlanBody(BaseModel):
    building: BuildingBody = Field(default_factory=BuildingBody)
    research: ResearchBody = Field(default_factory=ResearchBody)
    heroes: HeroesBody = Field(default_factory=HeroesBody)
    pets: PetsBody = Field(default_factory=PetsBody)
    troops: TroopsBody = Field(default_factory=TroopsBody)
    # Shared resource pool the coordinator spends. Defaults to the union of the
    # hero + pet namespaced balances (book:* / shard:* / pet_food / pet_shard:*);
    # add meat/wood/coal/iron/steel to let research contend instead of starving.
    balances: dict[str, int] | None = None
    # Idle channels. Defaults to building.free_queues construction lanes + one
    # research / hero / pet lane each.
    channels: list[ChannelBody] | None = None
    # Situational factors (all optional — omit for plain arbitration). These let
    # the calculator show schedule + quest awareness: a live event boosts its
    # reward domains, an open daily boosts its domain (harder near reset), a threat
    # suppresses the troop-exposing domains.
    events: list[EventWindowBody] = Field(default_factory=list)
    dailies: list[DailyTaskBody] = Field(default_factory=list)
    seconds_to_reset: float | None = None
    threat: SafetyBody | None = None


def _full_plan(body: FullPlanBody) -> dict[str, Any]:
    """Run every domain planner and arbitrate via the unified coordinator tick.

    Pure compute: the planners decide each domain's pick, then :func:`plan_cycle`
    folds in the schedule / quest / economy / safety / feedback factors and fills
    the channels. No ``role`` is passed to the cross-domain adapters — each planner
    already baked its own role into its pick (matches the per-domain endpoints).
    """
    from games.wos.core.building.planner import plan_builds
    from games.wos.core.coordinator import (
        CONSTRUCTION,
        HERO,
        PET,
        RESEARCH,
        TRAINING,
        Channel,
        DailyTask,
        EventWindow,
        ThreatState,
        plan_cycle,
    )
    from games.wos.core.pets.planner import plan_next as pet_plan_next
    from games.wos.core.research.planner import plan_next as research_plan_next
    from games.wos.heroes.heroes.planner import plan_next as hero_plan_next
    from games.wos.troops.planner import plan_next as training_plan_next

    bg, rg, hc, pc = (
        _building_graph(),
        _research_graph(),
        _hero_catalog(),
        _pet_catalog(),
    )
    slate = plan_builds(
        bg,
        body.building.levels,
        role=_role(body.building.role),
        resources=body.building.resources,
        free_queues=body.building.free_queues,
        goal_id=body.building.goal_id,
        goal_cap=body.building.goal_cap,
        # Construction-scoring windows live now → score builds for their event points.
        active_events=[w.slug for w in body.events if w.active],
    )
    rplan = research_plan_next(
        rg, body.research.levels, body.research.rc_level, role=_role(body.research.role)
    )
    hplan = hero_plan_next(
        hc,
        body.heroes.owned,
        body.heroes.resources,
        current_generation=_hero_current_generation(body.heroes),
        role=_role(body.heroes.role),
    )
    pplan = pet_plan_next(
        pc,
        body.pets.owned,
        body.pets.resources,
        server_days=body.pets.server_days,
        role=_role(body.pets.role),
    )
    tplan = training_plan_next(
        counts=body.troops.counts,
        max_tier=body.troops.max_tier,
        fc=body.troops.fc,
        target=body.troops.target,
    )

    if body.channels is not None:
        channels = [Channel(id=c.id, kind=c.kind) for c in body.channels]
    else:
        channels = [
            Channel(id=f"construction_{i + 1}", kind=CONSTRUCTION)
            for i in range(max(1, body.building.free_queues))
        ]
        channels += [
            Channel(id="research_1", kind=RESEARCH),
            Channel(id="hero_1", kind=HERO),
            Channel(id="pet_1", kind=PET),
            Channel(id="training_1", kind=TRAINING),
        ]

    if body.balances is not None:
        balances = dict(body.balances)
    else:
        balances = {**dict(body.heroes.resources), **dict(body.pets.resources)}

    plan = plan_cycle(
        channels=channels,
        balances=balances,
        build_slate=slate,
        build_graph=bg,
        research_plan=rplan,
        research_graph=rg,
        hero_plan=hplan,
        pet_plan=pplan,
        training_plan=tplan,
        event_windows=[EventWindow(**w.model_dump()) for w in body.events],
        daily_tasks=[DailyTask(**t.model_dump()) for t in body.dailies],
        seconds_to_reset=body.seconds_to_reset,
        threat=ThreatState(**body.threat.model_dump()) if body.threat is not None else None,
    )
    return {
        "plans": {
            "building": _asdict(slate),
            "research": _asdict(rplan),
            "heroes": _asdict(hplan),
            "pets": _asdict(pplan),
            "troops": _asdict(tplan),
        },
        "candidates": [_asdict(c) for c in plan.candidates],
        "decision": _asdict(plan.decision),
        "boosts": dict(plan.boosts),
        "calendar": _asdict(plan.calendar),
        "daily": _asdict(plan.daily),
        "economy": _asdict(plan.economy),
        "safety": _asdict(plan.safety),
        "feedback": _asdict(plan.feedback),
    }


@router.post("/full")
def post_full(body: FullPlanBody) -> dict[str, Any]:
    return _guard(lambda: _full_plan(body))


# --------------------------------------------------------------------------- #
# Projection — forward-simulate construction + research into an ETA timeline.
# "When will furnace 30 / RC unlock the next tier / power spike happen."
# --------------------------------------------------------------------------- #
class ProjectionBody(BaseModel):
    building_levels: dict[str, Any] = Field(default_factory=dict)
    research_levels: dict[str, int] = Field(default_factory=dict)
    construction_queues: int = 2
    role: str | None = None
    goal_id: str = "furnace"
    goal_cap: float = 30.0
    horizon_days: float | None = None    # cap the projection window (None = run to goal)
    # Owned heroes ({hero_id: {skill, star}}) → the construction/research-speed buffs
    # their skills grant shorten the projected build/research ETAs (a Construction-
    # Speed hero like Zinman literally pulls the milestones in).
    owned_heroes: dict[str, dict[str, int]] = Field(default_factory=dict)


@router.post("/projection")
def post_projection(body: ProjectionBody) -> dict[str, Any]:
    from games.wos.core.coordinator import project_cycle
    from games.wos.heroes.heroes.planner import active_city_buffs

    def run() -> dict[str, Any]:
        buffs = active_city_buffs(_hero_catalog(), body.owned_heroes)
        proj = project_cycle(
            build_graph=_building_graph(),
            build_levels=body.building_levels,
            research_graph=_research_graph(),
            research_levels=body.research_levels,
            construction_queues=body.construction_queues,
            role=_role(body.role),
            goal_id=body.goal_id,
            goal_cap=body.goal_cap,
            horizon_s=(body.horizon_days * 86_400.0) if body.horizon_days else None,
            construction_speed_pct=buffs.get("construction", 0.0),
            research_speed_pct=buffs.get("research", 0.0),
        )
        return _asdict(proj)

    return _guard(run)


# --------------------------------------------------------------------------- #
# Unlocks — the server-age schedule: current tier + what's opening next.
# --------------------------------------------------------------------------- #
class UnlocksBody(BaseModel):
    server_age_days: int | None = None
    profile: str = "beta"
    within_days: int = 30


@router.post("/unlocks")
def post_unlocks(body: UnlocksBody) -> dict[str, Any]:
    def run() -> dict[str, Any]:
        sched = _unlock_schedule(body.profile)
        days = body.server_age_days
        return {
            "profile": sched.profile,
            "server_age_days": days,
            "hero_generation": sched.hero_generation_at(days),
            "pet_generation": sched.pet_generation_at(days),
            "unlocked_modes": sched.unlocked_modes(days),
            "upcoming": [_asdict(e) for e in sched.upcoming(days, within_days=body.within_days)],
        }

    return _guard(run)


# --------------------------------------------------------------------------- #
# Fleet plan — run the full plan for every player at once: one glance at what
# each account should do next. Reads each player's saved planner state from
# state.db; pure compute, nothing dispatched.
# --------------------------------------------------------------------------- #
# Generous economy baseline so research/building contend instead of starving for
# lack of a resource reader. Per-player hero/pet namespaced balances are layered
# on top. Lower these (or wire a reader) to surface real bottlenecks.
_FLEET_ECON_BALANCES = {
    "meat": 50_000_000,
    "wood": 50_000_000,
    "coal": 20_000_000,
    "iron": 20_000_000,
    "steel": 10_000_000,
}


def _pick_label(plan: dict[str, Any], kind: str) -> str:
    if kind == "building":
        picks = plan.get("picks") or []
        if not picks:
            return "—"
        head = picks[0]
        extra = f" (+{len(picks) - 1})" if len(picks) > 1 else ""
        return f"{head['spec_id']}→{head['to_level']}{extra}"
    step = plan.get("step")
    if not step:
        return "—"
    if kind == "research":
        return str(step.get("name") or step.get("node_id"))
    if kind == "heroes":
        return f"{step['hero_id']} {step['kind']}→{step['to_level']}"
    if kind == "pets":
        return f"{step['pet_id']} {step['kind']}→{step['to_level']}"
    return "—"


def _fleet_row(player_id: str) -> dict[str, Any]:
    snap = _player_store(player_id).snapshot()
    domains = _planner_domains_from_snapshot(snap)
    body = FullPlanBody(
        building=BuildingBody(**domains["building"]),
        research=ResearchBody(**domains["research"]),
        heroes=HeroesBody(**domains["heroes"]),
        pets=PetsBody(**domains["pets"]),
        balances={
            **_FLEET_ECON_BALANCES,
            **dict(domains["heroes"].get("resources") or {}),
            **dict(domains["pets"].get("resources") or {}),
        },
    )
    out = _full_plan(body)
    plans, decision = out["plans"], out["decision"]
    return {
        "player_id": str(player_id),
        "nickname": snap.nickname,
        "picks": {
            "building": _pick_label(plans["building"], "building"),
            "research": _pick_label(plans["research"], "research"),
            "heroes": _pick_label(plans["heroes"], "heroes"),
            "pets": _pick_label(plans["pets"], "pets"),
        },
        "committed": len(decision["commits"]),
        "starved": [c["domain"] for c in decision["starved"]],
        "bottleneck": list(decision["bottleneck_resources"]),
        "error": None,
    }


@router.get("/fleet")
def get_fleet_plan(players: str = "") -> dict[str, Any]:
    """Full plan for every player (or a `players=a,b,c` subset)."""
    from api.services.players import list_player_ids

    pids = (
        [p.strip() for p in players.split(",") if p.strip()]
        if players.strip()
        else list_player_ids()
    )

    rows: list[dict[str, Any]] = []
    for pid in pids:
        try:
            rows.append(_fleet_row(pid))
        except Exception as exc:  # one bad account must not sink the fleet view
            rows.append({
                "player_id": str(pid),
                "nickname": "",
                "picks": {},
                "committed": 0,
                "starved": [],
                "bottleneck": [],
                "error": str(exc),
            })
    return {"rows": rows}
