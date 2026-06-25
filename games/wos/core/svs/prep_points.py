"""SvS Prep-Phase points scorer — how many SvS points a planned spend earns.

Whiteout Survival's Server-vs-Server prep phase runs 5 themed days; each unit of an
activity earns a fixed number of points, and the same activity can score on more
than one day (Refined Fire Crystals on Days 1/2/5, Mithril on Days 4/5, …). The
points-per-unit table is the real game fact wostools.net encodes; we re-encode it in
``games/wos/db/svs_prep.yaml`` and this module turns a planned ``{day: {activity:
qty}}`` into a points total + per-day breakdown, plus which day(s) an activity scores
on (so the operator can hold a resource for its best day).

Pure: reads the cached YAML, no Redis/device/mutation — safe behind the ``/planner``
calculator. This is **not** the power-derived event tilt in
``games/wos/core/building/planner/event_points.py`` (different model, different
consumer); the two coexist.

Honest about gaps: an activity that doesn't score on the requested day, or a troop
tier missing from the partial ``troop_points`` table, is reported in ``unknown`` —
never silently counted as zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# games/wos/core/svs/ → parents[2] = games/wos
DEFAULT_SVS_PREP_PATH = Path(__file__).resolve().parents[2] / "db" / "svs_prep.yaml"

TROOP_DAY = 4   # troop training/promotion scores on Day 4 (Hero Development)


@dataclass(frozen=True, slots=True)
class DaySpec:
    """One prep day: its theme name and the activity → points-per-unit table."""

    day: int
    name: str
    activities: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class SvsPrep:
    """The whole prep-phase scoring table."""

    days: Mapping[int, DaySpec]
    troop_points: Mapping[int, int]   # tier → points per troop trained (PARTIAL data)


@dataclass(frozen=True, slots=True)
class TroopPlanItem:
    """A planned Day-4 troop action. ``train`` a fresh tier, or ``promote`` between."""

    action: str                       # "train" | "promote"
    qty: int
    tier: int | None = None           # for train
    from_tier: int | None = None      # for promote
    to_tier: int | None = None        # for promote


@dataclass(frozen=True, slots=True)
class SvsLine:
    """One scored line of a plan."""

    day: int
    activity: str
    qty: int
    unit_points: int
    subtotal: int


@dataclass(frozen=True, slots=True)
class SvsScore:
    """Result of scoring a plan: grand total, per-day split, sorted breakdown."""

    total: int
    per_day: Mapping[int, int]
    breakdown: tuple[SvsLine, ...]
    unknown: tuple[str, ...]          # "<day>:<activity>" entries that don't score


@lru_cache(maxsize=2)
def load_svs_prep(path: str | Path | None = None) -> SvsPrep:
    """Load ``svs_prep.yaml`` → :class:`SvsPrep` (cached per process)."""
    p = Path(path) if path else DEFAULT_SVS_PREP_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    days: dict[int, DaySpec] = {}
    for day, spec in (doc.get("days") or {}).items():
        d = int(day)
        acts = {
            str(a): int(v)
            for a, v in ((spec or {}).get("activities") or {}).items()
        }
        days[d] = DaySpec(day=d, name=str((spec or {}).get("name", "")), activities=acts)
    troop = {int(t): int(v) for t, v in (doc.get("troop_points") or {}).items()}
    return SvsPrep(days=days, troop_points=troop)


def points_for(activity: str, day: int, prep: SvsPrep | None = None) -> int | None:
    """Points per unit of ``activity`` on ``day`` (None if it doesn't score that day)."""
    prep = prep or load_svs_prep()
    spec = prep.days.get(int(day))
    if spec is None:
        return None
    return spec.activities.get(str(activity))


def days_for(activity: str, prep: SvsPrep | None = None) -> tuple[int, ...]:
    """Which prep days ``activity`` scores on — for "hold it for its best day" hints."""
    prep = prep or load_svs_prep()
    return tuple(
        sorted(day for day, spec in prep.days.items() if str(activity) in spec.activities)
    )


def troop_train_points(tier: int, prep: SvsPrep | None = None) -> int | None:
    """Day-4 points for training one fresh troop of ``tier`` (None if tier unknown)."""
    prep = prep or load_svs_prep()
    return prep.troop_points.get(int(tier))


def troop_promote_points(
    from_tier: int, to_tier: int, prep: SvsPrep | None = None
) -> int | None:
    """Day-4 points for promoting one troop ``from_tier`` → ``to_tier`` (the difference)."""
    prep = prep or load_svs_prep()
    lo = prep.troop_points.get(int(from_tier))
    hi = prep.troop_points.get(int(to_tier))
    if lo is None or hi is None:
        return None
    return hi - lo


def _troop_line(item: TroopPlanItem, prep: SvsPrep) -> tuple[SvsLine | None, str | None]:
    """Score one troop action → (line, unknown_label). Exactly one is non-None."""
    if item.action == "train" and item.tier is not None:
        unit = troop_train_points(item.tier, prep)
        label = f"troop_train_t{int(item.tier)}"
    elif item.action == "promote" and item.from_tier is not None and item.to_tier is not None:
        unit = troop_promote_points(item.from_tier, item.to_tier, prep)
        label = f"troop_promote_t{int(item.from_tier)}_t{int(item.to_tier)}"
    else:
        return None, f"{TROOP_DAY}:troop_bad_action({item.action})"
    if unit is None:
        return None, f"{TROOP_DAY}:{label}"
    qty = int(item.qty)
    return SvsLine(TROOP_DAY, label, qty, unit, unit * qty), None


def score_plan(
    plan: Mapping[str | int, Mapping[str, int]] | None,
    troops: Sequence[TroopPlanItem] = (),
    prep: SvsPrep | None = None,
) -> SvsScore:
    """Score a ``{day: {activity: qty}}`` plan (+ optional Day-4 troop actions).

    Returns the grand total, the per-day split, a subtotal-sorted breakdown, and the
    ``unknown`` list of ``"<day>:<activity>"`` entries that don't score on that day
    (or troop tiers absent from the partial table) — surfaced, not silently dropped.
    """
    prep = prep or load_svs_prep()
    lines: list[SvsLine] = []
    unknown: list[str] = []
    per_day: dict[int, int] = {}

    for day, acts in (plan or {}).items():
        d = int(day)
        for activity, qty in (acts or {}).items():
            q = int(qty)
            if q == 0:
                continue
            unit = points_for(activity, d, prep)
            if unit is None:
                unknown.append(f"{d}:{activity}")
                continue
            sub = unit * q
            lines.append(SvsLine(d, str(activity), q, unit, sub))
            per_day[d] = per_day.get(d, 0) + sub

    for item in troops:
        if int(item.qty) == 0:
            continue
        line, bad = _troop_line(item, prep)
        if line is None:
            unknown.append(bad or f"{TROOP_DAY}:troop")
            continue
        lines.append(line)
        per_day[line.day] = per_day.get(line.day, 0) + line.subtotal

    lines.sort(key=lambda ln: (-ln.subtotal, ln.day, ln.activity))
    return SvsScore(
        total=sum(per_day.values()),
        per_day=dict(sorted(per_day.items())),
        breakdown=tuple(lines),
        unknown=tuple(sorted(unknown)),
    )
