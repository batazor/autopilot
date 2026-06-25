"""King of the Icefield (KoI) prep-points scoring.

Pure what-if scorer over the points-per-item table in ``games/wos/db/koi_points.yaml``
(re-encoded from wostools.net). Isolated sibling of ``games/wos/core/svs``; KoI is 7 days
and scores troops on Days 4 and 6. Live phase reading + spend-timing are deferred.
"""
from __future__ import annotations

from .koi_points import (
    DaySpec,
    KoiLine,
    KoiPrep,
    KoiScore,
    KoiTroopPlanItem,
    days_for,
    load_koi_points,
    points_for,
    score_plan,
    troop_promote_points,
    troop_train_points,
)

__all__ = [
    "DaySpec",
    "KoiLine",
    "KoiPrep",
    "KoiScore",
    "KoiTroopPlanItem",
    "days_for",
    "load_koi_points",
    "points_for",
    "score_plan",
    "troop_promote_points",
    "troop_train_points",
]
