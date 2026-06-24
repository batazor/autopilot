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

from .hero_xp import MAX_HERO_LEVEL, level_cost, level_furnace_gate
from .policy import (
    RARITY_BOOK_COST,
    RARITY_BOOK_TIER,
    SKILL_VALUE_FACTOR,
    economy_buff_uplift,
    generation_factor,
    hero_value,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import HeroSpec

# Investment kinds
PROMOTE_STAR = "promote_star"     # spends shard:<hero_id>
UPGRADE_SKILL = "upgrade_skill"   # spends book:<tier>
LEVEL_UP = "level_up"             # spends hero_xp (Furnace-gated)

LEVEL_VALUE_FACTOR = 0.95         # a level step is worth ~95% of a star step

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
    detail: str = ""           # e.g. "construction +3%" — the economy buff a skill unlocks


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


def _buff_detail(spec: HeroSpec, from_skill: int) -> str:
    """Human note for the buff a skill upgrade unlocks (``"construction +3%"``)."""
    parts = [
        f"{sk.category} +{sk.marginal(from_skill):g}%"
        for sk in spec.economy_skills
        if sk.marginal(from_skill) > 0
    ]
    return ", ".join(parts)


def plan_next(
    catalog: Mapping[str, HeroSpec],
    owned: Mapping[str, Mapping[str, int]],
    resources: Mapping[str, int],
    *,
    current_generation: int | None = None,
    hero_generation: Mapping[str, int] | None = None,
    role: RoleProfile | None = None,
    weights: Mapping[str, float] | None = None,
    furnace_level: int | None = None,
) -> HeroPlan:
    """Pick the next hero investment (star / skill / level) by value, within the budget.

    With ``furnace_level`` set, a ``level_up`` step (spends ``hero_xp``) competes with
    star/skill — it reads the hero's current ``level`` and is gated by the Furnace band
    of the next level. ``furnace_level=None`` (the default) omits level steps entirely,
    leaving today's star/skill behaviour unchanged.
    """
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
        # A skill upgrade also unlocks the next step of any economy buff this hero
        # carries (construction/research/training/… speed) — value that throughput,
        # gen-scaled so we don't pour books into a fading hero for its buff alone.
        gen_factor = generation_factor(hero_generation.get(hero_id), current_generation)
        skill_uplift = economy_buff_uplift(spec, skill, role, marginal=True) * gen_factor
        proposals = [
            (PROMOTE_STAR, star + 1, value,
             {f"shard:{hero_id}": spec.shard_cost(star)}, ""),
            (UPGRADE_SKILL, skill + 1, value * SKILL_VALUE_FACTOR + skill_uplift,
             {f"book:{tier}": RARITY_BOOK_COST.get(spec.rarity, 4)}, _buff_detail(spec, skill)),
        ]
        # Level the hero (spends Hero XP) — only when the Furnace is known and clears
        # the next level's band; reads the otherwise-ignored owned ``level``.
        level = _owned(owned, hero_id, "level")
        if furnace_level is not None and level < MAX_HERO_LEVEL \
                and furnace_level >= level_furnace_gate(level + 1):
            proposals.append((
                LEVEL_UP, level + 1, value * LEVEL_VALUE_FACTOR,
                {"hero_xp": level_cost(level, level + 1)}, "",
            ))
        for kind, to_level, val, cost, detail in proposals:
            affordable = all(int(resources.get(r, 0)) >= amt for r, amt in cost.items())
            cand = HeroCandidate(hero_id, kind, to_level, val, cost, affordable, detail)
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


@dataclass(frozen=True, slots=True)
class HeroUpgradeRoadmap:
    """Total cost to bring one hero current → target (the calculator's headline)."""

    cost: Mapping[str, int]   # hero_xp + shard:<id> + book:<tier>
    steps: int                # level-ups + star promotions + skill upgrades


def _dim(state: Mapping[str, int], key: str) -> int:
    try:
        return int(state.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def hero_upgrade_roadmap(
    spec: HeroSpec,
    current: Mapping[str, int],
    target: Mapping[str, int],
) -> HeroUpgradeRoadmap:
    """Total Hero XP + shards + books to raise ``spec`` across level / star / skill.

    ``current`` / ``target`` carry ``level`` / ``star`` / ``skill``; a dimension whose
    target is ≤ current contributes nothing. Stars reuse the hero's real per-star
    ``shard_cost``; levels the Hero XP ladder; skills the representative book cost.
    """
    cost: dict[str, int] = {}
    steps = 0

    cur_lvl, tgt_lvl = _dim(current, "level"), _dim(target, "level")
    if tgt_lvl > cur_lvl:
        cost["hero_xp"] = level_cost(cur_lvl, tgt_lvl)
        steps += tgt_lvl - cur_lvl

    cur_star, tgt_star = _dim(current, "star"), _dim(target, "star")
    shards = sum(spec.shard_cost(s) for s in range(cur_star, tgt_star))
    if shards:
        cost[f"shard:{spec.id}"] = shards
    steps += max(0, tgt_star - cur_star)

    cur_skill, tgt_skill = _dim(current, "skill"), _dim(target, "skill")
    skill_steps = max(0, tgt_skill - cur_skill)
    if skill_steps:
        tier = RARITY_BOOK_TIER.get(spec.rarity, "rare")
        cost[f"book:{tier}"] = skill_steps * RARITY_BOOK_COST.get(spec.rarity, 4)
    steps += skill_steps

    return HeroUpgradeRoadmap(cost=cost, steps=steps)
