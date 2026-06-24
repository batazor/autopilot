"""Value-greedy hero-investment planner.

Decides where the next books/shards go from the static hero catalog
(``games/wos/db/heroes/*.yaml``), what's owned, and current balances — gated by the
server generation and tilted by the account role. Pure and testable; live readers
are deferred.
"""
from __future__ import annotations

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
    NONE,
    PROMOTE_STAR,
    SELECTED,
    UPGRADE_SKILL,
    HeroCandidate,
    HeroPlan,
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
    "NONE",
    "PROMOTE_STAR",
    "RARITY_WEIGHT",
    "SELECTED",
    "UPGRADE_SKILL",
    "HeroCandidate",
    "HeroPlan",
    "HeroSkill",
    "HeroSpec",
    "active_city_buffs",
    "catalog_subclass_index",
    "economy_buff_uplift",
    "generation_factor",
    "hero_value",
    "load_hero_catalog",
    "parse_economy_skills",
    "parse_shard_tiers",
    "plan_next",
]
