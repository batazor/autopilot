"""SvS (Server vs Server) prep-phase scoring.

Pure what-if scorer over the points-per-item table in ``games/wos/db/svs_prep.yaml``
(re-encoded from wostools.net). Live SvS phase reading + spend-timing integration are
deferred; this package only answers "how many SvS points would this plan earn?".
"""
from __future__ import annotations

from .prep_points import (
    DaySpec,
    SvsLine,
    SvsPrep,
    SvsScore,
    TroopPlanItem,
    days_for,
    load_svs_prep,
    points_for,
    score_plan,
    troop_promote_points,
    troop_train_points,
)

__all__ = [
    "DaySpec",
    "SvsLine",
    "SvsPrep",
    "SvsScore",
    "TroopPlanItem",
    "days_for",
    "load_svs_prep",
    "points_for",
    "score_plan",
    "troop_promote_points",
    "troop_train_points",
]
