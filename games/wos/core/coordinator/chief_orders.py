"""Chief's House orders — pick the order that matches what's happening now.

The Chief's House issues timed server-wide buffs (the `chief_orders` module already
enacts them). Smart play ties the *order* to the *moment*: enact the construction
buff during a construction event, the healing buff when troops are hurt (post-combat
/ SvS), the mobilization buff during a training/mobilization event — so the buff
lands where it converts to event points or recovery, not at random.

Pure: consumes the same signals the other coordinator layers already produce — the
calendar's ``active_categories`` and the safety layer's injured / pvp flags — and
returns a priority-ordered list of order ids (matching the ``chief.order.<id>``
regions). Context-matched orders lead; the rest follow as the existing default combo
so nothing is wasted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Order ids — match the chief_orders module's `chief.order.<id>` region suffixes.
RUSH_JOB = "rush"                       # construction speed
URGENT_MOBILIZATION = "urgent"          # troop training / mobilization
COMPREHENSIVE_CARE = "comprehensive_care"   # healing speed
DOUBLE_TIME = "double_time"             # research speed
PRODUCTIVITY = "productivity"           # resource production / gathering
FESTIVITIES = "festivities"             # generic / standalone

# Always-useful fallback order when nothing is specifically indicated.
DEFAULT_ORDER = (PRODUCTIVITY, RUSH_JOB, DOUBLE_TIME, URGENT_MOBILIZATION,
                 COMPREHENSIVE_CARE, FESTIVITIES)


@dataclass(frozen=True, slots=True)
class ChiefOrderPlan:
    """Priority-ordered chief orders to enact, with why the leaders were picked."""

    recommended: tuple[str, ...]
    reasons: Mapping[str, str] = field(default_factory=dict)


def recommend_orders(
    *,
    active_categories: Sequence[str] = (),
    injured: int = 0,
    pvp_window: bool = False,
) -> ChiefOrderPlan:
    """Order the chief orders by fit to the current event / situation.

    ``active_categories`` come from :func:`events.calendar_bias`; ``injured`` /
    ``pvp_window`` from the safety :class:`ThreatState`.
    """
    cats = set(active_categories)
    leaders: list[str] = []
    reasons: dict[str, str] = {}

    def add(order_id: str, reason: str) -> None:
        if order_id not in leaders:
            leaders.append(order_id)
            reasons[order_id] = reason

    if "construction" in cats or "any_power" in cats:
        add(RUSH_JOB, "construction event live")
    if "training" in cats or "any_power" in cats:
        add(URGENT_MOBILIZATION, "training/mobilization event live")
    if "research" in cats or "any_power" in cats:
        add(DOUBLE_TIME, "research event live")
    if injured > 0 or pvp_window:
        add(COMPREHENSIVE_CARE, "wounded troops / PvP window" if injured else "PvP window")
    if "gather" in cats:
        add(PRODUCTIVITY, "gather event live")

    # Append the default combo for anything not already prioritised.
    for order_id in DEFAULT_ORDER:
        if order_id not in leaders:
            leaders.append(order_id)

    return ChiefOrderPlan(recommended=tuple(leaders), reasons=reasons)
