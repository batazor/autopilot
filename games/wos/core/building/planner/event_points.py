"""Event-points scoring for building upgrades — what an upgrade is worth *now*.

Whiteout Survival's recurring points events (SvS prep, King of Icefield / State of
Power, Hall of Chiefs, Power Up, …) score the **power** you gain inside their
window: power from a completed building upgrade counts for every event that
rewards construction (or any-power). We already carry ``building_power`` per level
in the build graph, so the points a candidate upgrade would net while a window is
live is ``power_gained * weight``, where ``weight`` is the per-event modelling
factor from ``games/wos/db/event_scoring.yaml``.

Pure: graph spec + current/target rank + the active event slugs in, an int point
estimate out. Honest about missing data — a level whose ``building_power`` is
``null`` (the Furnace 1-30 ladder lacks power; the Fire-Crystal ladders carry it)
contributes 0, so the score scales with whatever power coverage the data has.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .model import BuildingSpec

# games/wos/core/building/planner/ → parents[3] = games/wos
DEFAULT_EVENT_SCORING_PATH = Path(__file__).resolve().parents[3] / "db" / "event_scoring.yaml"

# Categories a *building* upgrade can score in: its own (construction) plus the
# any-power events where construction power also counts (Power Up, Hall of Chiefs).
BUILD_CATEGORIES = ("construction", "any_power")


@lru_cache(maxsize=2)
def load_event_scoring(path: str | Path | None = None) -> dict[str, dict[str, float]]:
    """Load ``event_scoring.yaml`` → ``{slug: {category: weight}}``."""
    p = Path(path) if path else DEFAULT_EVENT_SCORING_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out: dict[str, dict[str, float]] = {}
    for slug, cats in (doc.get("events") or {}).items():
        out[str(slug)] = {
            str(cat): float((spec or {}).get("weight", 0.0) or 0.0)
            for cat, spec in (cats or {}).items()
        }
    return out


def event_weight(
    active_slugs: Sequence[str],
    scoring: Mapping[str, Mapping[str, float]] | None = None,
    *,
    categories: Sequence[str] = BUILD_CATEGORIES,
) -> float:
    """Best scoring weight across the active events for any of ``categories``.

    0.0 when no active event rewards the activity (no points window → no bonus).
    """
    table = scoring if scoring is not None else load_event_scoring()
    best = 0.0
    for slug in active_slugs:
        cats = table.get(slug)
        if not cats:
            continue
        for cat in categories:
            w = cats.get(cat)
            if w and w > best:
                best = w
    return best


def level_power_at(spec: BuildingSpec, rank: float) -> int:
    """Total building power at ``rank``: the highest non-null ``building_power`` of a
    level at or below it (carries forward across ``null`` gaps). 0 when unbuilt or no
    level on the ladder has power data."""
    power = 0
    for lvl in spec.levels:                      # levels are rank-sorted ascending
        if lvl.rank > rank:
            break
        if lvl.power is not None:
            power = lvl.power
    return power


def power_gain(spec: BuildingSpec, from_rank: float, to_rank: float) -> int:
    """Power added by upgrading ``from_rank`` → ``to_rank`` (clamped ≥ 0)."""
    return max(0, level_power_at(spec, to_rank) - level_power_at(spec, from_rank))


def upgrade_points(
    gained_power: int,
    active_slugs: Sequence[str],
    scoring: Mapping[str, Mapping[str, float]] | None = None,
) -> int:
    """Event points an upgrade nets if landed in the active window: ``power * weight``.

    0 when no active event scores construction (or any-power) — the off-window case.
    """
    if gained_power <= 0:
        return 0
    return int(gained_power * event_weight(active_slugs, scoring))
