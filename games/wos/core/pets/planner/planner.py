"""Value-greedy pet-investment planner: where do the next food/shards go?

Pure decision over the static pet catalog, what's owned, balances, and the server
age. A pet is only a candidate once **unlocked** — server age ≥ its threshold AND
its prerequisite pet is at the required level (the data-driven progression gate).
Among unlocked pets it ranks each next step — refine (costs that pet's shards) or
upgrade its skill (costs pet food) — by value (rarity × role) and picks the
highest-value affordable one.

Resource keys: ``pet_shard:<pet_id>`` (pet-specific) and ``pet_food`` (shared pool).
Live reads (owned pets + levels, balances, server age) are deferred.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .policy import RARITY_FOOD_COST, RARITY_SHARD_COST, pet_value

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import PetAdvancement, PetSpec

# Investment kinds
REFINE = "refine"               # spends pet_shard:<id>
UPGRADE_SKILL = "upgrade_skill" # spends pet_food

# Plan reasons
SELECTED = "selected"
INSUFFICIENT_RESOURCES = "insufficient_resources"
LOCKED = "locked"               # nothing unlocked yet (server too young / prereqs unmet)
NONE = "none"

SKILL_VALUE_FACTOR = 0.9


@dataclass(frozen=True, slots=True)
class PetCandidate:
    pet_id: str
    kind: str
    to_level: int
    value: float
    cost: Mapping[str, int]
    affordable: bool


@dataclass(frozen=True, slots=True)
class PetPlan:
    step: PetCandidate | None
    reason: str
    candidates: tuple[PetCandidate, ...] = field(default_factory=tuple)


def _level(owned: Mapping[str, Mapping[str, int]], pet_id: str, key: str) -> int:
    entry = owned.get(pet_id) or {}
    try:
        return int(entry.get(key, 0))
    except (TypeError, ValueError):
        return 0


def is_unlocked(
    spec: PetSpec, server_days: int | None, owned: Mapping[str, Mapping[str, int]]
) -> bool:
    """Server age ≥ threshold AND the prerequisite pet is at the required level."""
    if spec.unlock_days is not None and server_days is not None and server_days < spec.unlock_days:
        return False
    if spec.prereq is not None:
        pre_id, pre_level = spec.prereq
        if _level(owned, pre_id, "level") < pre_level:
            return False
    return True


def plan_next(
    catalog: Mapping[str, PetSpec],
    owned: Mapping[str, Mapping[str, int]],
    resources: Mapping[str, int],
    *,
    server_days: int | None = None,
    role: RoleProfile | None = None,
    weights: Mapping[str, float] | None = None,
) -> PetPlan:
    """Pick the next pet investment (refine or skill) by value, within the budget."""
    candidates: list[PetCandidate] = []
    best: PetCandidate | None = None
    best_key: tuple[float, int] | None = None
    any_unlocked = False

    for pet_id in sorted(catalog):
        spec = catalog[pet_id]
        if not is_unlocked(spec, server_days, owned):
            continue
        any_unlocked = True
        value = pet_value(spec, role=role, weights=weights)
        refine = _level(owned, pet_id, "refine")
        skill = _level(owned, pet_id, "skill")
        proposals = (
            (REFINE, refine + 1, value,
             {f"pet_shard:{pet_id}": RARITY_SHARD_COST.get(spec.rarity, 4)}),
            (UPGRADE_SKILL, skill + 1, value * SKILL_VALUE_FACTOR,
             {"pet_food": RARITY_FOOD_COST.get(spec.rarity, 3)}),
        )
        for kind, to_level, val, cost in proposals:
            affordable = all(int(resources.get(r, 0)) >= amt for r, amt in cost.items())
            cand = PetCandidate(pet_id, kind, to_level, val, cost, affordable)
            candidates.append(cand)
            if affordable:
                key = (val, -sum(cost.values()))
                if best_key is None or key > best_key:
                    best, best_key = cand, key

    candidates.sort(key=lambda c: (-c.value, c.pet_id, c.kind))
    if best is not None:
        reason = SELECTED
    elif not any_unlocked:
        reason = LOCKED
    elif candidates:
        reason = INSUFFICIENT_RESOURCES
    else:
        reason = NONE
    return PetPlan(step=best, reason=reason, candidates=tuple(candidates[:8]))


# --- Roadmap (honest, real-data totals — no material costs) ------------------
# A pet advances every 10 levels; each advance = +1 advancement score (SvS/KoI Day-3/5
# points) AND consumes Taming Manuals / Energizing Potions / Strengthening Serums per
# the real per-max-level-tier table in db/pets/advancement_costs.yaml (whiteoutsurvival.wiki).
# The roadmap totals those real materials + advancement score, plus the at-max Troop
# ATK/DEF % and refinement % for pets taken to their max level (the only stat the data
# carries — no per-level curve). Pet Food per level and Wild-Mark refinement are out of scope.

ADVANCEMENT_EVERY = 10   # a pet hits an advancement threshold every 10 levels


@dataclass(frozen=True, slots=True)
class PetRoadmap:
    """Totals to raise a set of pets current → target (advancement materials + score)."""

    advancements: int          # advancement thresholds crossed across all pets
    advancement_score: int     # == advancements (each advance = +1 score)
    svs_points: int            # advancement_score × per-score SvS value (Day 3/5)
    materials: Mapping[str, int]   # taming_manual / energizing_potion / strengthening_serum totals
    troop_attack_pct: float    # Σ at-max ATK% for pets taken to their max level
    troop_defense_pct: float   # Σ at-max DEF% for pets taken to their max level
    refinement_pct: float      # Σ at-max refinement% for pets taken to their max level
    per_pet: tuple[Mapping[str, object], ...]
    missing: tuple[str, ...]   # target pet ids absent from the catalog


def _advancements(from_level: int, to_level: int) -> int:
    """Advancement thresholds crossed raising ``from_level`` → ``to_level``."""
    return max(0, to_level // ADVANCEMENT_EVERY - from_level // ADVANCEMENT_EVERY)


def pet_roadmap(
    catalog: Mapping[str, PetSpec],
    current: Mapping[str, int],
    target: Mapping[str, int],
    *,
    advancement: PetAdvancement | None = None,
) -> PetRoadmap:
    """Total advancement materials + score (→ SvS pts) + at-max stats, current→target.

    ``current``/``target`` are ``{pet_id: level}``. For each targeted pet the level is
    clamped to its ``max_level``; for every advancement milestone crossed (the real
    per-max-level-tier table from ``advancement_costs.yaml``) we sum the Taming Manuals /
    Energizing Potions / Strengthening Serums and count +1 advancement score. At-max Troop
    ATK/DEF % and refinement % are summed **only** for pets taken to their max level (the
    data has no per-level stat curve). A target id absent from the catalog → ``missing``."""
    from .model import load_pet_advancement

    adv_data = advancement if advancement is not None else load_pet_advancement()
    advancements = 0
    materials: dict[str, int] = {}
    atk = dfn = refine = 0.0
    per_pet: list[Mapping[str, object]] = []
    missing: list[str] = []

    for pet_id, tgt in (target or {}).items():
        spec = catalog.get(str(pet_id))
        if spec is None:
            missing.append(str(pet_id))
            continue
        cur = int((current or {}).get(pet_id, 0) or 0)
        top = min(int(tgt), spec.max_level)
        table = adv_data.table_for(spec.max_level)
        crossed = [lvl for lvl in sorted(table) if cur < lvl <= top]
        if table:
            pet_adv = len(crossed)
            for lvl in crossed:
                for mat, qty in table[lvl].items():
                    materials[mat] = materials.get(mat, 0) + int(qty)
        else:                                    # no material table for this tier
            pet_adv = _advancements(cur, top)
        at_max = top >= spec.max_level and top > cur
        if at_max:
            atk += spec.troop_attack_pct
            dfn += spec.troop_defense_pct
            refine += spec.max_refinement_pct
        advancements += pet_adv
        per_pet.append({
            "pet_id": spec.id, "from_level": cur, "to_level": top,
            "advancements": pet_adv, "at_max": at_max,
        })

    try:
        from games.wos.core.svs.prep_points import points_for as _svs_points_for
        per_score = _svs_points_for("pet_advancement_score", 3) or 50
    except Exception:
        per_score = 50

    return PetRoadmap(
        advancements=advancements,
        advancement_score=advancements,
        svs_points=advancements * int(per_score),
        materials=dict(sorted(materials.items())),
        troop_attack_pct=round(atk, 2),
        troop_defense_pct=round(dfn, 2),
        refinement_pct=round(refine, 2),
        per_pet=tuple(per_pet),
        missing=tuple(missing),
    )
