"""Value-greedy Hero Gear planner (multi-track: enhance / mastery / widget).

Decides which (piece, track) step to upgrade next from the 3 ladders (config in
``games/wos/db/hero_gear.yaml``, numbers from the sheet-sourced
``db/gear/enhancement.yaml``) + per-piece per-track levels + material balances, each
track gated by its own Furnace unlock. Pure and testable; live readers deferred.
:func:`hero_gear_roadmap` totals current→targets (the calculator's development view).
"""
from __future__ import annotations

from .model import HeroGearData, TrackLadder, load_hero_gear_data
from .planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    NONE,
    SELECTED,
    HeroGearCandidate,
    HeroGearPlan,
    HeroGearRoadmap,
    hero_gear_roadmap,
    plan_next,
)
from .policy import TRACK_WEIGHT, hero_gear_value

__all__ = [
    "INSUFFICIENT_RESOURCES",
    "LOCKED",
    "NONE",
    "SELECTED",
    "TRACK_WEIGHT",
    "HeroGearCandidate",
    "HeroGearData",
    "HeroGearPlan",
    "HeroGearRoadmap",
    "TrackLadder",
    "hero_gear_roadmap",
    "hero_gear_value",
    "load_hero_gear_data",
    "plan_next",
]
