"""Alliance Showdown points scoring + coordinator investment tilt.

Pure what-if scorer over the points-per-item table in
``games/wos/db/alliance_showdown_points.yaml`` (re-encoded from wostools.net), plus
:func:`stage_domain_tilt` — the per-stage ``{domain: multiplier}`` map the coordinator
merges with its other event boosts so investment routes to the domain that scores most
points in the live stage. Live stage reading is deferred; the standalone calculator
takes the planned spend (and current stage) as operator input.
"""
from __future__ import annotations

from .showdown_points import (
    AS_TILT_WEIGHT,
    ITEM_DOMAIN,
    TROOP_STAGES,
    ShowdownLine,
    ShowdownPoints,
    ShowdownScore,
    StageSpec,
    TroopPlanItem,
    load_showdown_points,
    points_for,
    score_plan,
    stage_domain_tilt,
    stages_for,
    troop_promote_points,
    troop_train_points,
)

__all__ = [
    "AS_TILT_WEIGHT",
    "ITEM_DOMAIN",
    "TROOP_STAGES",
    "ShowdownLine",
    "ShowdownPoints",
    "ShowdownScore",
    "StageSpec",
    "TroopPlanItem",
    "load_showdown_points",
    "points_for",
    "score_plan",
    "stage_domain_tilt",
    "stages_for",
    "troop_promote_points",
    "troop_train_points",
]
