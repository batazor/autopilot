"""Value weighting for Chief Charm investment — even-leveling × composition × role.

Config-as-code. Every charm levels on the *same* cost/power curve, so the decision
is *which slot* to raise. The policy drives **even leveling** (a lagging charm — a
lower current level — is worth more, since the cost curve rises faster than power),
tilted by army **composition** (the troop type the slot buffs, reusing the troop
planner's target) and the account **role** (charms are combat power → a fighter
lifts them, a farm drops them). Power is reported per candidate but deliberately
does NOT drive the pick: the source lacks it above L11, and cost-efficiency already
implies even leveling — robust to that gap.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.ladder import even_leveling_value
from games.wos.troops.planner import DEFAULT_TARGET as TROOP_TARGET

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

CHARM_BASE = 100.0          # internal value scale (the coordinator bands the domain)
CHARM_ROLE_CATEGORY = "battle"   # charms boost troop Lethality+Health → combat power


def charm_value(
    troop_type: str,
    to_level: int,
    *,
    max_level: int,
    role: RoleProfile | None = None,
    target: Mapping[str, float] | None = None,
) -> float:
    """Value of raising a ``troop_type`` charm to ``to_level`` (even-leveling × role)."""
    return even_leveling_value(
        to_level, max_level=max_level,
        composition=(target or TROOP_TARGET).get(troop_type, 0.33),
        base=CHARM_BASE, role=role, role_category=CHARM_ROLE_CATEGORY,
    )
