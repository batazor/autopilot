"""Value model for Intel events — how good is one on-screen marker?

The Intel screen shows a handful of action pins (markers) that refresh on a timer
(~every few hours). Each costs a fixed slice of the shared stamina pool to clear
and drops loot whose richness tracks the pin's **colour** (gold > purple > blue)
and **kind** (the rarer horned-skull / camp pins beat ordinary fight / skull pins).

This mirrors the deterministic colour/kind ordering already used to *pick* a marker
on screen (``games/wos/intel/exec.py:_pick_marker``), but expressed as continuous
weights so the planner can *rank a whole batch* by value and spend a stamina budget
on the best ones first. Pure constants + one value function; no IO, no cv2.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .model import IntelEvent

# Loot richness by pin colour (higher = better). Same order as
# exec._MARKER_COLOR_PRIORITY, inverted into a weight.
MARKER_COLOR_WEIGHT: dict[str, float] = {
    "gold": 1.0,
    "purple": 0.65,
    "blue": 0.35,
    "green": 0.35,
    "unknown": 0.15,
}

# Within a colour, the rarer/special pins (horned skull, camp, beast) out-reward
# the ordinary fight / skull pins. Same order as exec._MARKER_KIND_PRIORITY.
MARKER_KIND_WEIGHT: dict[str, float] = {
    "skull_horned": 1.3,
    "camp": 1.3,
    "beast": 1.2,
    "fight": 1.0,
    "skull": 1.0,
}

# Colours an operator typically treats as "worth the stamina". Used by the
# ``priority_only`` filter; the value-greedy budget already takes these first.
PRIORITY_COLORS: tuple[str, ...] = ("gold", "purple")

# Mirror games/wos/core/stamina/budget.yaml :: demands[id=intel_events].
DEFAULT_COST_PER_EVENT = 10        # stamina spent clearing one marker
DEFAULT_DAILY_QUOTA = 10           # "10 intel events per day"


def intel_value(
    event: IntelEvent,
    *,
    weights: Mapping[str, float] | None = None,
) -> float:
    """Loot value of one marker: ``colour_weight × kind_weight``.

    ``weights`` may override individual colour/kind keys (e.g. an event that
    rewards a specific pin type). The detection ``score`` is *not* part of value —
    it only breaks ties in the planner (a confident match, not a richer reward).
    """
    color_w = MARKER_COLOR_WEIGHT.get(event.color, MARKER_COLOR_WEIGHT["unknown"])
    kind_w = MARKER_KIND_WEIGHT.get(event.kind, 1.0)
    if weights:
        color_w = float(weights.get(event.color, color_w))
        kind_w = float(weights.get(event.kind, kind_w))
    return color_w * kind_w
