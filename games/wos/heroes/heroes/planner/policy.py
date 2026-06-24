"""Value weighting for hero investment — rarity × role × server generation.

Config-as-code. A hero's worth combines: rarity (Legendary > Epic > Rare), the
account role (a fighter values Combat heroes, a farm values Growth/gathering
specialists — and Growth heroes feed the economy loop, so they matter early for
everyone), and the **server generation** — heroes power-creep, so an older-gen
hero's value decays and a far-behind one is skipped entirely (don't pour Mythic
books into an obsolete hero). Generation isn't in the wiki data; it's supplied as
a per-hero map + the current server generation.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.roles import multiplier as role_multiplier

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import HeroSkill, HeroSpec

RARITY_WEIGHT: dict[str, float] = {"Legendary": 100.0, "Epic": 60.0, "Rare": 35.0}

# sub_class → role category (Combat tilts with fighters, Growth with farms/economy).
SUBCLASS_CATEGORY: dict[str, str] = {"Combat": "battle", "Growth": "economy"}

# Which book tier a hero's skills consume, and a representative cost per skill step.
RARITY_BOOK_TIER: dict[str, str] = {"Legendary": "mythic", "Epic": "epic", "Rare": "rare"}
RARITY_BOOK_COST: dict[str, int] = {"Legendary": 10, "Epic": 6, "Rare": 4}

# Generation decay: each generation behind costs 25% value; ≥4 behind → obsolete.
GEN_DECAY = 0.25
GEN_MAX_BEHIND = 4
SKILL_VALUE_FACTOR = 0.9          # a skill step is worth ~90% of a star step


# --- Economy/efficiency buffs from hero skills -------------------------------
# Some hero skills accelerate city throughput (construction / research / training /
# gathering speed, infirmary healing). The hero planner values that throughput when
# distributing skill books, so a construction-speed hero earns extra value on a
# growth account — its skill upgrades feed the very work the economy planners run.
# Weight = value points per +1% of buff; the role category each buff serves routes
# it through the (down-weight-only) role multiplier so growth/economy buffs tilt to
# farms and training/heal tilts to fighters.
ECONOMY_BUFF_WEIGHT: dict[str, float] = {
    "construction": 0.8,
    "research": 0.8,
    "training": 0.6,
    "gather": 0.5,
    "heal": 0.3,
}
ECONOMY_BUFF_ROLE_CATEGORY: dict[str, str] = {
    "construction": "economy", "research": "economy", "gather": "economy",
    "training": "battle", "heal": "battle",
}


def _skill_uplift(skill: HeroSkill, amount: float, role: RoleProfile | None) -> float:
    """Value of ``amount`` percent of ``skill``'s buff, weighted + role-biased."""
    if amount <= 0:
        return 0.0
    weight = ECONOMY_BUFF_WEIGHT.get(skill.category, 0.0)
    bias = 1.0
    if role is not None:
        bias = role_multiplier(role, ECONOMY_BUFF_ROLE_CATEGORY.get(skill.category, "economy"))
    return amount * weight * bias


def economy_buff_uplift(
    spec: HeroSpec, skill_level: int, role: RoleProfile | None, *, marginal: bool
) -> float:
    """Hero value added by its economy skills.

    ``marginal=True`` scores only the buff gained by the *next* skill level (the
    reward for spending the book now); ``marginal=False`` scores the full unrealised
    potential above ``skill_level`` (how much the hero is worth developing overall).
    """
    total = 0.0
    for sk in spec.economy_skills:
        amount = sk.marginal(skill_level) if marginal else sk.remaining(skill_level)
        total += _skill_uplift(sk, amount, role)
    return total


def active_city_buffs(
    catalog: Mapping[str, HeroSpec], owned: Mapping[str, Mapping[str, int]]
) -> dict[str, float]:
    """Total active city buff % per category from the owned heroes' skill levels.

    The aggregate the economy planners can divide build/research/training times by
    (a construction-speed sum here → faster build ETAs). Pure; reads each hero's
    ``skill`` level from ``owned``."""
    out: dict[str, float] = {}
    for hid, spec in catalog.items():
        if not spec.economy_skills:
            continue
        try:
            level = int((owned.get(hid) or {}).get("skill", 0) or 0)
        except (TypeError, ValueError):
            level = 0
        for sk in spec.economy_skills:
            out[sk.category] = out.get(sk.category, 0.0) + sk.buff_at(level)
    return out


def generation_factor(gen: int | None, current: int | None) -> float:
    """Value multiplier for a hero ``gen`` against the ``current`` server gen."""
    if gen is None or current is None or gen >= current:
        return 1.0
    behind = current - gen
    if behind >= GEN_MAX_BEHIND:
        return 0.0                # obsolete → don't invest
    return max(0.1, 1.0 - GEN_DECAY * behind)


def hero_value(
    spec: HeroSpec,
    *,
    role: RoleProfile | None = None,
    generation: int | None = None,
    current_generation: int | None = None,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Base value of investing in a hero (0 → don't, e.g. obsolete generation)."""
    table = weights if weights is not None else RARITY_WEIGHT
    base = table.get(spec.rarity, 10.0)
    if role is not None:
        base *= role_multiplier(role, SUBCLASS_CATEGORY.get(spec.sub_class, "battle"))
    return base * generation_factor(generation, current_generation)
