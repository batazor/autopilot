"""Value weighting for pet investment — rarity × role, by skill category.

Config-as-code. A pet's worth combines rarity (SSR > ordinary) and the account
role, tilted by what the pet's skill does: march/gather/construction pets help the
economy (a farm values them), combat pets help a fighter, stamina/utility pets are
universal. The server-age unlock gate is handled in the planner (not here) — it
decides *availability*, this decides *value once available*.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.roles import multiplier as role_multiplier

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import PetSpec

PET_RARITY_WEIGHT: dict[str, float] = {"SSR": 100.0, "": 55.0}

# Pet skill category → role category for the role tilt.
CATEGORY_ROLE: dict[str, str] = {
    "march": "economy",
    "gather": "economy",
    "construction": "economy",
    "stamina": "growth",       # universal — never demoted by role
    "combat": "battle",
}

# Representative dev costs (pet shards are pet-specific; food is a shared pool).
RARITY_SHARD_COST: dict[str, int] = {"SSR": 8, "": 4}
RARITY_FOOD_COST: dict[str, int] = {"SSR": 6, "": 3}


def pet_value(
    spec: PetSpec,
    *,
    role: RoleProfile | None = None,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Base value of investing in a pet (rarity × role-by-category)."""
    table = weights if weights is not None else PET_RARITY_WEIGHT
    base = table.get(spec.rarity, 40.0)
    if role is not None:
        base *= role_multiplier(role, CATEGORY_ROLE.get(spec.category, "battle"))
    return base
