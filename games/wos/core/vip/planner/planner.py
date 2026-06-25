"""VIP progression planner — the next VIP level-up + the develop-to-target roadmap.

VIP is a single linear track (VIP 1 → 12), so unlike the multi-slot charm/gear
planners there is no "which slot" decision and no even-leveling: the only step is
``current_level → current_level + 1``. :func:`plan_next` returns that step with its
``vip_points`` cost (the remaining XP to the next level), gated by the budget;
:func:`vip_roadmap` totals current→target and decomposes it into VIP Points items
(the calculator's "how many points / which items to reach VIP X" answer).

Cost key: ``vip_points`` (VIP Points apply 1:1 as VIP XP, matching
db/items/vip_points.yaml). Live readers populate ``vip.level`` (sync_vip_level);
the count of VIP-Points items owned is not read yet, so the coordinator channel is
inert on live state — the daily spend stays automated by the ``vip.daily`` scenario.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from games.wos.core.roles import multiplier as role_multiplier

from .model import load_vip_levels

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import VipData

# Plan reasons
SELECTED = "selected"
LOCKED = "locked"                       # VIP not unlocked yet (Furnace gate, default none)
INSUFFICIENT_RESOURCES = "insufficient_resources"
NONE = "none"                           # already at the target / max VIP level

VIP_BASE = 100.0                        # internal value scale (the coordinator bands the domain)
VIP_ROLE_CATEGORY = "growth"            # VIP buffs (build/research/training speed) → universal


@dataclass(frozen=True, slots=True)
class VipCandidate:
    to_level: int
    xp_needed: int            # remaining VIP XP to reach ``to_level`` (net of current_xp)
    value: float
    cost: Mapping[str, int]   # {"vip_points": xp_needed}
    affordable: bool


@dataclass(frozen=True, slots=True)
class VipPlan:
    step: VipCandidate | None
    reason: str
    candidates: tuple[VipCandidate, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class VipRoadmap:
    """Totals to develop current→target (the calculator's output)."""

    cost: Mapping[str, int]            # {"vip_points": total_xp} (overview-compatible)
    total_xp: int                      # VIP XP remaining to the target
    steps: int                         # number of level-ups
    item_plan: Mapping[int, int]       # VIP Points denomination → count (greedy)
    leftover_xp: int                   # sub-smallest-denomination remainder (< min item)
    per_level: tuple[Mapping[str, int], ...]   # [{"to_level": L, "xp": gross step cost}, …]


def vip_value(to_level: int, *, max_level: int, role: RoleProfile | None = None) -> float:
    """Value of reaching ``to_level`` — base × role tilt (growth ⇒ universal)."""
    base = VIP_BASE
    if role is not None:
        base *= role_multiplier(role, VIP_ROLE_CATEGORY)
    return base


def _decompose(total_xp: int, denoms: tuple[int, ...]) -> tuple[dict[int, int], int]:
    """Greedy decomposition of ``total_xp`` into VIP Points denominations."""
    item_plan: dict[int, int] = {}
    remaining = max(0, int(total_xp))
    for d in sorted((int(x) for x in denoms if int(x) > 0), reverse=True):
        n, remaining = divmod(remaining, d)
        if n:
            item_plan[d] = n
    return item_plan, remaining


def _clamp_level(level: int, data: VipData) -> int:
    """VIP 1 is the 0-XP base; clamp an unknown/0 level into the table's range."""
    return max(1, min(int(level), data.max_level))


def plan_next(
    current_level: int,
    current_xp: int = 0,
    resources: Mapping[str, int] | None = None,
    *,
    target_level: int | None = None,
    role: RoleProfile | None = None,
    furnace_level: int | None = None,
    data: VipData | None = None,
) -> VipPlan:
    """The next VIP level-up (``current_level → +1``) within the ``vip_points`` budget.

    ``current_xp`` is VIP XP already accumulated toward the next level. ``resources``
    supplies the budget as ``{"vip_points": n}``. ``furnace_level`` gates the feature
    (VIP has no gate by default → pass ``None`` to skip). ``target_level`` caps the
    push (defaults to the table's max).
    """
    data = data if data is not None else load_vip_levels()
    resources = resources or {}

    if furnace_level is not None and furnace_level < data.unlock_furnace_level:
        return VipPlan(step=None, reason=LOCKED)

    cur = _clamp_level(current_level, data)
    cap = data.max_level if target_level is None else min(int(target_level), data.max_level)
    if cur >= cap:
        return VipPlan(step=None, reason=NONE)

    xp_to_next = data.xp_to_next(cur)
    if xp_to_next is None:                       # already at the table cap
        return VipPlan(step=None, reason=NONE)

    xp_needed = max(0, int(xp_to_next) - max(0, int(current_xp)))
    cost = {"vip_points": xp_needed}
    affordable = int(resources.get("vip_points", 0)) >= xp_needed
    cand = VipCandidate(
        to_level=cur + 1,
        xp_needed=xp_needed,
        value=vip_value(cur + 1, max_level=data.max_level, role=role),
        cost=cost,
        affordable=affordable,
    )
    reason = SELECTED if affordable else INSUFFICIENT_RESOURCES
    return VipPlan(step=cand if affordable else None, reason=reason, candidates=(cand,))


def vip_roadmap(
    current_level: int,
    current_xp: int,
    target_level: int,
    *,
    data: VipData | None = None,
) -> VipRoadmap:
    """Total VIP XP + the VIP Points item breakdown to go current→``target_level``."""
    data = data if data is not None else load_vip_levels()
    cur = _clamp_level(current_level, data)
    tgt = max(cur, min(int(target_level), data.max_level))

    total_xp = max(0, data.cumulative_xp(tgt) - data.cumulative_xp(cur) - max(0, int(current_xp)))
    per_level = tuple(
        {"to_level": lvl, "xp": data.cumulative_xp(lvl) - data.cumulative_xp(lvl - 1)}
        for lvl in range(cur + 1, tgt + 1)
    )
    item_plan, leftover = _decompose(total_xp, data.point_items)

    return VipRoadmap(
        cost={"vip_points": total_xp},
        total_xp=total_xp,
        steps=tgt - cur,
        item_plan=item_plan,
        leftover_xp=leftover,
        per_level=per_level,
    )
