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

from games.wos.core.roles import multiplier as role_multiplier
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
    """Value of raising a ``troop_type`` charm to ``to_level``.

    Lower target levels score higher (even leveling); scaled by the troop type's
    composition share and the role's combat tilt.
    """
    comp = (target or TROOP_TARGET).get(troop_type, 0.33)
    recency = max(1, max_level - int(to_level) + 1)        # lagging charms first
    value = CHARM_BASE * comp * recency
    if role is not None:
        value *= role_multiplier(role, CHARM_ROLE_CATEGORY)
    return value
