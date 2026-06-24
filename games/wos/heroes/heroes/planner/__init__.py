"""Value-greedy hero-investment planner.

Decides where the next books/shards go from the static hero catalog
(``games/wos/db/heroes/*.yaml``), what's owned, and current balances — gated by the
server generation and tilted by the account role. Pure and testable; live readers
are deferred.
"""
from __future__ import annotations

from .hero_xp import MAX_HERO_LEVEL, level_cost, level_furnace_gate, load_hero_xp
from .model import (
    HeroSkill,
    HeroSpec,
    catalog_subclass_index,
    load_hero_catalog,
    parse_economy_skills,
    parse_shard_tiers,
)
from .planner import (
    INSUFFICIENT_RESOURCES,
    LEVEL_UP,
    NONE,
    PROMOTE_STAR,
    SELECTED,
    UPGRADE_SKILL,
    HeroCandidate,
    HeroPlan,
    HeroUpgradeRoadmap,
    hero_upgrade_roadmap,
    plan_next,
)
from .policy import (
    ECONOMY_BUFF_WEIGHT,
    RARITY_WEIGHT,
    active_city_buffs,
    economy_buff_uplift,
    generation_factor,
    hero_value,
)

__all__ = [
    "ECONOMY_BUFF_WEIGHT",
    "INSUFFICIENT_RESOURCES",
    "LEVEL_UP",
    "MAX_HERO_LEVEL",
    "NONE",
    "PROMOTE_STAR",
    "RARITY_WEIGHT",
    "SELECTED",
    "UPGRADE_SKILL",
    "HeroCandidate",
    "HeroPlan",
    "HeroSkill",
    "HeroSpec",
    "HeroUpgradeRoadmap",
    "active_city_buffs",
    "catalog_subclass_index",
    "economy_buff_uplift",
    "generation_factor",
    "hero_upgrade_roadmap",
    "hero_value",
    "level_cost",
    "level_furnace_gate",
    "load_hero_catalog",
    "load_hero_xp",
    "parse_economy_skills",
    "parse_shard_tiers",
    "plan_next",
]
