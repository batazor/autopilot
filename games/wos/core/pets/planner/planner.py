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

    from .model import PetSpec

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
