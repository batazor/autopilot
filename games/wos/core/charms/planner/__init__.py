"""Value-greedy Chief Charms planner.

Decides which charm slot to raise next from the shared upgrade table
(``games/wos/db/chief_charms.yaml``) + per-slot levels + material balances, gated by
the Furnace-25 unlock and tilted by army composition + role. Pure and testable; live
readers are deferred. :func:`charm_roadmap` totals current→target (the calculator's
development view).
"""
from __future__ import annotations

from .model import CharmData, CharmLevel, load_charm_data
from .planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    NONE,
    SELECTED,
    CharmCandidate,
    CharmPlan,
    CharmRoadmap,
    charm_roadmap,
    plan_next,
)
from .policy import charm_value

__all__ = [
    "INSUFFICIENT_RESOURCES",
    "LOCKED",
    "NONE",
    "SELECTED",
    "CharmCandidate",
    "CharmData",
    "CharmLevel",
    "CharmPlan",
    "CharmRoadmap",
    "charm_roadmap",
    "charm_value",
    "load_charm_data",
    "plan_next",
]
