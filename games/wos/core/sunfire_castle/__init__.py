"""Sunfire Castle territory — fixed global-map facts + buff-tower capture planner.

``territory`` re-encodes the wostools territory-planner facts (structures + buff towers
+ zone bands on the 1200×1200 global map); ``tower_plan`` ranks which buff towers an
account should help capture, by role. Both are pure / calculator-only — no live readers.
"""
from __future__ import annotations

from games.wos.core.sunfire_castle.territory import (
    BUFF_TYPES,
    DEFAULT_TERRITORY_PATH,
    Structure,
    Territory,
    Tower,
    Zone,
    iter_structures,
    iter_towers,
    iter_zones,
    load_territory,
)
from games.wos.core.sunfire_castle.tower_plan import (
    BUFF_CATEGORY,
    TowerCandidate,
    TowerRanking,
    buff_category,
    rank_towers,
    tower_value,
)

__all__ = [
    "BUFF_CATEGORY",
    "BUFF_TYPES",
    "DEFAULT_TERRITORY_PATH",
    "Structure",
    "Territory",
    "Tower",
    "TowerCandidate",
    "TowerRanking",
    "Zone",
    "buff_category",
    "iter_structures",
    "iter_towers",
    "iter_zones",
    "load_territory",
    "rank_towers",
    "tower_value",
]
