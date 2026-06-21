"""Daybreak Island planner.

Decides *which island thing to build/upgrade next* from the static island data
(``games/wos/db/island/*.yaml``) plus the player's live island state. The Tree of
Life is the spearhead (the island's Furnace), gated by a Prosperity threshold +
Life Essence cost rather than a prerequisite building; when it can't advance the
planner pivots to prosperity-efficient, role-tilted decorations. Pure and
testable; live readers (tree level, prosperity, LE balance, owned decorations)
are deferred.
"""
from __future__ import annotations

from .model import (
    Decoration,
    IslandData,
    StatBonus,
    Structure,
    TreeLevel,
    load_island_data,
)
from .planner import (
    ALL_MAXED,
    DECORATION,
    INSUFFICIENT_LIFE_ESSENCE,
    LIGHTHOUSE,
    PRODUCER,
    SELECTED,
    TREE,
    IslandCandidate,
    IslandPlan,
    IslandState,
    plan_island_next,
)
from .policy import (
    PROSPERITY_UNIT,
    RARITY_BUFF_WEIGHT,
    TREE_WEIGHT,
    buff_value,
    decoration_value,
)

__all__ = [
    "ALL_MAXED",
    "DECORATION",
    "INSUFFICIENT_LIFE_ESSENCE",
    "LIGHTHOUSE",
    "PRODUCER",
    "PROSPERITY_UNIT",
    "RARITY_BUFF_WEIGHT",
    "SELECTED",
    "TREE",
    "TREE_WEIGHT",
    "Decoration",
    "IslandCandidate",
    "IslandData",
    "IslandPlan",
    "IslandState",
    "StatBonus",
    "Structure",
    "TreeLevel",
    "buff_value",
    "decoration_value",
    "load_island_data",
    "plan_island_next",
]
