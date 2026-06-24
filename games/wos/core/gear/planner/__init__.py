"""Value-greedy Chief Gear planner.

Decides which gear piece to upgrade next from the shared ladder
(``games/wos/db/chief_gear.yaml``) + per-piece levels + material balances, gated by
the Furnace-22 unlock and tilted by army composition + role. Pure and testable; live
readers are deferred. :func:`gear_roadmap` totals current→target (the calculator's
development view).
"""
from __future__ import annotations

from .model import GearData, GearLevel, load_gear_data
from .planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    NONE,
    SELECTED,
    GearCandidate,
    GearPlan,
    GearRoadmap,
    gear_roadmap,
    plan_next,
)
from .policy import gear_value

__all__ = [
    "INSUFFICIENT_RESOURCES",
    "LOCKED",
    "NONE",
    "SELECTED",
    "GearCandidate",
    "GearData",
    "GearLevel",
    "GearPlan",
    "GearRoadmap",
    "gear_roadmap",
    "gear_value",
    "load_gear_data",
    "plan_next",
]
