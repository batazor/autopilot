"""War Academy → trainable troop tier: what the camps can build, from research state.

The camps train up to T10 on their own; the highest tiers are unlocked in the War
Academy by completing the per-type unlock research — ``helios_<type>`` (T11 Helios,
itself gated by War Academy FC5). Completing it (level ≥ 1) is what lets the camp
train that type at T11. This pure helper turns the research levels into the
``max_tier`` the troop planner takes, so War Academy progress drives what we train.

T12 (Exalted) is intentionally not unlocked here yet: ``games/wos/db/troops.yaml``
has no T12 stats, so the troop planner couldn't value it even if the cap allowed it.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

from .planner import TROOP_TYPES

BASE_TIER = 10                                   # camps reach T10 without the War Academy
HELIOS_TIER = 11                                 # helios_<type> research → T11 unlocked
# Per-type T11 unlock research node ids (see games/wos/db/research.yaml).
HELIOS_NODE = {t: f"helios_{t}" for t in TROOP_TYPES}


def unlocked_max_tier(research_levels: Mapping[str, int] | None = None) -> dict[str, int]:
    """Per-type trainable tier cap from research: T11 once ``helios_<type>`` ≥ 1, else T10.

    ``research_levels`` maps research ``node_id`` → current level (0 = not researched);
    ``None``/empty → every camp capped at :data:`BASE_TIER`.
    """
    levels = research_levels or {}
    out: dict[str, int] = {}
    for troop in TROOP_TYPES:
        unlocked = int(levels.get(HELIOS_NODE[troop], 0) or 0) >= 1
        out[troop] = HELIOS_TIER if unlocked else BASE_TIER
    return out
