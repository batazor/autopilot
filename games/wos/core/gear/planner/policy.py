"""Value weighting for Chief Gear investment — even-leveling × composition × role.

Same shape as the charm policy: every piece climbs the *same* cost/power ladder, so
the decision is *which piece* to upgrade. Drives **even leveling** (a lagging piece
is worth more), tilted by army **composition** (the troop type the piece buffs,
reusing the troop planner's target) and the account **role** (gear is combat power →
a fighter lifts it, a farm drops it). Power is reported per candidate but doesn't
drive the pick — cost-efficiency already implies even leveling.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.ladder import even_leveling_value
from games.wos.troops.planner import DEFAULT_TARGET as TROOP_TARGET

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

GEAR_BASE = 100.0           # internal value scale (the coordinator bands the domain)
GEAR_ROLE_CATEGORY = "battle"    # gear boosts troop attack/defense → combat power


def gear_value(
    troop_type: str,
    to_level: int,
    *,
    max_level: int,
    role: RoleProfile | None = None,
    target: Mapping[str, float] | None = None,
) -> float:
    """Value of raising a ``troop_type`` gear piece to ``to_level`` (lagging first)."""
    return even_leveling_value(
        to_level, max_level=max_level,
        composition=(target or TROOP_TARGET).get(troop_type, 0.33),
        base=GEAR_BASE, role=role, role_category=GEAR_ROLE_CATEGORY,
    )
