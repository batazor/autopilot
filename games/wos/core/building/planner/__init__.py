"""Furnace-first building planner.

Decides *which building to upgrade next* from the static dependency graph
(``games/wos/db/buildings/*.yaml``) plus the player's current levels. Pure and
testable; the live-levels reader and navigate-and-tap execution are deferred.
"""
from __future__ import annotations

from .event_points import (
    event_weight,
    load_event_scoring,
    power_gain,
    upgrade_points,
)
from .model import (
    ITEM_RESOURCE,
    BuildGraph,
    BuildingSpec,
    LevelReq,
    level_rank,
    load_graph,
    parse_amount,
    parse_duration,
    parse_prerequisites,
    resource_name,
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
    apply_speed,
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
    "ITEM_RESOURCE",
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
    "apply_speed",
    "evaluate_queue_rental",
    "event_weight",
    "level_rank",
    "load_event_scoring",
    "load_graph",
    "parse_amount",
    "parse_duration",
    "parse_prerequisites",
    "plan_builds",
    "plan_next",
    "power_gain",
    "project_multi_schedule",
    "project_schedule",
    "resource_name",
    "upgrade_points",
]
