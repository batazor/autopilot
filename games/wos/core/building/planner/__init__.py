"""Furnace-first building planner.

Decides *which building to upgrade next* from the static dependency graph
(``games/wos/db/buildings/*.yaml``) plus the player's current levels. Pure and
testable; the live-levels reader and navigate-and-tap execution are deferred.
"""
from __future__ import annotations

from .model import (
    BuildGraph,
    BuildingSpec,
    LevelReq,
    level_rank,
    load_graph,
    parse_amount,
    parse_duration,
    parse_prerequisites,
)
from .planner import (
    ALL_MAXED,
    BLOCKED,
    DEFAULT_GOAL,
    GOAL_REACHED,
    GOAL_UNKNOWN,
    INSUFFICIENT_RESOURCES,
    SELECTED,
    BuildCandidate,
    BuildPlan,
    BuildSlate,
    BuildStep,
    plan_builds,
    plan_next,
)
from .queue_rental import (
    QueueRentalDecision,
    evaluate_queue_rental,
)
from .schedule import (
    DEFAULT_MAX_STEPS,
    BuildSchedule,
    ScheduledBuild,
    project_multi_schedule,
    project_schedule,
)

__all__ = [
    "ALL_MAXED",
    "BLOCKED",
    "DEFAULT_GOAL",
    "DEFAULT_MAX_STEPS",
    "GOAL_REACHED",
    "GOAL_UNKNOWN",
    "INSUFFICIENT_RESOURCES",
    "SELECTED",
    "BuildCandidate",
    "BuildGraph",
    "BuildPlan",
    "BuildSchedule",
    "BuildSlate",
    "BuildStep",
    "BuildingSpec",
    "LevelReq",
    "QueueRentalDecision",
    "ScheduledBuild",
    "evaluate_queue_rental",
    "level_rank",
    "load_graph",
    "parse_amount",
    "parse_duration",
    "parse_prerequisites",
    "plan_builds",
    "plan_next",
    "project_multi_schedule",
    "project_schedule",
]
