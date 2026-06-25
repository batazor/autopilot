"""Value-greedy pet-investment planner.

Decides where the next pet food/shards go from the static pet catalog
(``games/wos/db/pets/*.yaml``), what's owned, balances and the server age —
gated by each pet's data-driven unlock (age + prerequisite pet) and tilted by the
account role. Pure and testable; live readers are deferred.
"""
from __future__ import annotations

from .model import (
    PetAdvancement,
    PetSpec,
    catalog_category_index,
    categorize_skill,
    load_pet_advancement,
    load_pet_catalog,
    parse_unlock,
)
from .planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    NONE,
    REFINE,
    SELECTED,
    UPGRADE_SKILL,
    PetCandidate,
    PetPlan,
    PetRoadmap,
    is_unlocked,
    pet_roadmap,
    plan_next,
)
from .policy import CATEGORY_ROLE, PET_RARITY_WEIGHT, pet_value

__all__ = [
    "CATEGORY_ROLE",
    "INSUFFICIENT_RESOURCES",
    "LOCKED",
    "NONE",
    "PET_RARITY_WEIGHT",
    "REFINE",
    "SELECTED",
    "UPGRADE_SKILL",
    "PetAdvancement",
    "PetCandidate",
    "PetPlan",
    "PetRoadmap",
    "PetSpec",
    "catalog_category_index",
    "categorize_skill",
    "is_unlocked",
    "load_pet_advancement",
    "load_pet_catalog",
    "parse_unlock",
    "pet_roadmap",
    "pet_value",
    "plan_next",
]
