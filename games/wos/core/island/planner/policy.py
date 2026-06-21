"""Value weighting for the Daybreak Island planner — decoration buff × role,
plus the Prosperity premium that drives the Tree-of-Life climb.

Config-as-code, mirroring the other planners. Two things have value on the island:

* **Buff value** — a decoration's permanent main-game bonus, weighted by rarity
  (Mythic > Epic > Rare) and tilted by the account [[account-role-profiles|role]]
  (a fighter values troop decorations, a farm values gathering ones). Construction
  and research speed map to ``growth`` — universal profit, never demoted.
* **Prosperity value** — Prosperity gates the Tree of Life. When the next tree
  level is *prosperity-blocked*, Prosperity becomes the bottleneck and every point
  of it is worth ``PROSPERITY_UNIT``; the planner then prefers prosperity-efficient
  decorations (this is the island analog of the build planner's bottleneck repair).
  When the tree is *not* blocked, Prosperity still accrues but isn't urgent, so it
  drops out and decorations compete on buff value alone.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.roles import BATTLE, ECONOMY, GROWTH
from games.wos.core.roles import multiplier as role_multiplier

if TYPE_CHECKING:
    from games.wos.core.roles import RoleProfile

    from .model import Decoration

# The Tree of Life is the spearhead — its upgrade outranks any single decoration
# when it's ready+affordable (like the Furnace in the build planner). Kept above
# the richest decoration buff (Mythic ≈ 120) so the tree stays the spine.
TREE_WEIGHT = 150.0

# A Life-Essence producer (Lumber Camp) — pure throughput; valued as economy so a
# farm leans on it. Below the tree, above a stray buff decoration when the LE
# economy is the limiter.
PRODUCER_WEIGHT = 65.0

# Per-point worth of Prosperity *while the tree is prosperity-blocked*. Tuned so a
# Rare decoration (~1000 prosperity) clearly outvalues its own buff when blocked,
# making the planner climb toward the threshold instead of stalling.
PROSPERITY_UNIT = 0.05

# Buff base weight by decoration rarity (before the role multiplier). Mythic carry
# the biggest percentages, Rare the smallest — the rarity ladder stands in for the
# per-stat magnitude (we don't model every % individually in v1).
RARITY_BUFF_WEIGHT: dict[str, float] = {
    "mythic": 120.0,
    "epic": 70.0,
    "rare": 40.0,
    "uncommon": 0.0,   # no buff
    "common": 0.0,     # no buff
}

# Decoration buff kind → role weight category. Gathering is economy; construction
# and research speed are universal growth; everything combat-flavoured is battle.
KIND_CATEGORY: dict[str, str] = {
    # economy — resource throughput
    "resource_gather": ECONOMY,
    "iron_gather": ECONOMY,
    "meat_gather": ECONOMY,
    "coal_gather": ECONOMY,
    "wood_gather": ECONOMY,
    "hunting_march": ECONOMY,
    # growth — universal profit
    "construction": GROWTH,
    "research": GROWTH,
    "deployment": GROWTH,
    # battle — combat power / army
    "heal_speed": BATTLE,
    "march_speed": BATTLE,
    "training": BATTLE,
    "infantry_attack": BATTLE,
    "infantry_defense": BATTLE,
    "lancer_attack": BATTLE,
    "lancer_defense": BATTLE,
    "marksman_attack": BATTLE,
    "marksman_defense": BATTLE,
}


def buff_value(deco: Decoration, role: RoleProfile | None = None) -> float:
    """Permanent-buff worth of a decoration, biased by ``role`` (0 for no-buff)."""
    base = RARITY_BUFF_WEIGHT.get(deco.rarity, 0.0)
    if base == 0.0:
        return 0.0
    if role is not None:
        base *= role_multiplier(role, KIND_CATEGORY.get(deco.kind, BATTLE))
    return base


def decoration_value(
    deco: Decoration, role: RoleProfile | None = None, *, prosperity_blocked: bool = False
) -> float:
    """Total value of building ``deco`` now: buff value, plus — only while the
    Tree of Life is prosperity-blocked — the Prosperity it contributes."""
    value = buff_value(deco, role)
    if prosperity_blocked:
        value += deco.prosperity * PROSPERITY_UNIT
    return value
