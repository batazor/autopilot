"""Value weighting for Hero Gear investment — track-weighted even-leveling × role.

Three tracks on different scales (enhance 1-100, mastery 1-20, widget 1-10), so the
recency is **normalised** by each track's max level to compare apples to apples. A
track weight orders the tracks (enhance is the base/biggest power, mastery multiplies
on top, widget is the smallest), composition tilts by the piece's troop type, and the
role gives the combat tilt. Power isn't modelled here (the calculator's headline is
materials); value leans on track weight + even leveling.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.ladder import even_leveling_value
from games.wos.troops.planner import DEFAULT_TARGET as TROOP_TARGET

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

HERO_GEAR_BASE = 100.0
HERO_GEAR_ROLE_CATEGORY = "battle"      # hero gear boosts troop combat stats

# Order the tracks: enhance (the foundation) before mastery (multiplier) before widget.
TRACK_WEIGHT: dict[str, float] = {"enhance": 1.0, "mastery": 0.8, "widget": 0.6}


def hero_gear_value(
    troop_type: str,
    track: str,
    to_level: int,
    *,
    max_level: int,
    role: RoleProfile | None = None,
    target: Mapping[str, float] | None = None,
) -> float:
    """Value of the next step of ``track`` on a ``troop_type`` piece (lagging first).

    Recency is **normalised** by the track's own max level so the three tracks
    (enhance 100 / mastery 20 / widget 10) compare apples-to-apples, then scaled by
    the track weight (enhance > mastery > widget).
    """
    return TRACK_WEIGHT.get(track, 0.5) * even_leveling_value(
        to_level, max_level=max_level,
        composition=(target or TROOP_TARGET).get(troop_type, 0.33),
        base=HERO_GEAR_BASE, role=role, role_category=HERO_GEAR_ROLE_CATEGORY,
        normalize=True,
    )
