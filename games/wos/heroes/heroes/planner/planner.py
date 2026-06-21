"""Value-greedy hero-investment planner: where do the next books/shards go?

Pure decision over the static hero catalog, what the player owns, and current
book/shard balances. Among heroes worth investing in (generation-relevant), it
ranks each possible next step — promote a star (costs that hero's shards) or
upgrade a skill (costs books of the hero's rarity tier) — by value (rarity × role
× generation) and picks the highest-value one the player can afford.

Resource keys are namespaced because the resources are tiered/per-hero, exactly as
the game splits them: ``shard:<hero_id>`` (hero-specific) and ``book:<tier>``
(mythic/epic/rare). Live reads (owned heroes, balances, current generation) are
deferred; this module only answers "invest where next?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .policy import (
    RARITY_BOOK_COST,
    RARITY_BOOK_TIER,
    SKILL_VALUE_FACTOR,
    hero_value,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import HeroSpec

# Investment kinds
PROMOTE_STAR = "promote_star"     # spends shard:<hero_id>
UPGRADE_SKILL = "upgrade_skill"   # spends book:<tier>

# Plan reasons
SELECTED = "selected"
INSUFFICIENT_RESOURCES = "insufficient_resources"   # candidates exist, none affordable
NONE = "none"                                        # nothing worth investing in


@dataclass(frozen=True, slots=True)
class HeroCandidate:
    hero_id: str
    kind: str                  # PROMOTE_STAR | UPGRADE_SKILL
    to_level: int
    value: float
    cost: Mapping[str, int]
    affordable: bool


@dataclass(frozen=True, slots=True)
class HeroPlan:
    step: HeroCandidate | None
    reason: str
    candidates: tuple[HeroCandidate, ...] = field(default_factory=tuple)


def _owned(owned: Mapping[str, Mapping[str, int]], hero_id: str, key: str) -> int:
    entry = owned.get(hero_id) or {}
    try:
        return int(entry.get(key, 0))
    except (TypeError, ValueError):
        return 0


def plan_next(
    catalog: Mapping[str, HeroSpec],
    owned: Mapping[str, Mapping[str, int]],
    resources: Mapping[str, int],
    *,
    current_generation: int | None = None,
    hero_generation: Mapping[str, int] | None = None,
    role: RoleProfile | None = None,
    weights: Mapping[str, float] | None = None,
) -> HeroPlan:
    """Pick the next hero investment (star or skill) by value, within the budget."""
    hero_generation = hero_generation or {}
    candidates: list[HeroCandidate] = []
    best: HeroCandidate | None = None
    best_key: tuple[float, int] | None = None

    for hero_id in sorted(catalog):
        spec = catalog[hero_id]
        value = hero_value(
            spec, role=role, generation=hero_generation.get(hero_id),
            current_generation=current_generation, weights=weights,
        )
        if value <= 0:
            continue                                   # obsolete generation → skip

        star = _owned(owned, hero_id, "star")
        skill = _owned(owned, hero_id, "skill")
        tier = RARITY_BOOK_TIER.get(spec.rarity, "rare")
        proposals = (
            (PROMOTE_STAR, star + 1, value, {f"shard:{hero_id}": spec.shard_cost(star)}),
            (UPGRADE_SKILL, skill + 1, value * SKILL_VALUE_FACTOR,
             {f"book:{tier}": RARITY_BOOK_COST.get(spec.rarity, 4)}),
        )
        for kind, to_level, val, cost in proposals:
            affordable = all(int(resources.get(r, 0)) >= amt for r, amt in cost.items())
            cand = HeroCandidate(hero_id, kind, to_level, val, cost, affordable)
            candidates.append(cand)
            if affordable:
                key = (val, -sum(cost.values()))
                if best_key is None or key > best_key:
                    best, best_key = cand, key

    candidates.sort(key=lambda c: (-c.value, c.hero_id, c.kind))
    if best is not None:
        reason = SELECTED
    elif candidates:
        reason = INSUFFICIENT_RESOURCES
    else:
        reason = NONE
    return HeroPlan(step=best, reason=reason, candidates=tuple(candidates[:8]))
