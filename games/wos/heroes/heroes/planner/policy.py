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

    from .model import HeroSpec

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
