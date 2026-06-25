"""King of the Icefield (KoI) points scorer — how many KoI points a planned spend earns.

KoI is a 7-day cross-state development race; each unit of an activity earns a fixed number
of points, and the same activity can score on more than one day (Mithril on Days 2/4/5,
Refined Fire Crystals on Days 1/2/5/7, …). The points-per-unit table is the real game fact
wostools.net encodes; we re-encode it in ``games/wos/db/koi_points.yaml`` and this module
turns a planned ``{day: {activity: qty}}`` into a points total + per-day breakdown, plus
which day(s) an activity scores on.

Sibling of (but independent from) ``games/wos/core/svs/prep_points.py`` — the user chose an
isolated copy. KoI differs in two structural ways: 7 days, and troop training/promotion
scores on Days 4 AND 6 (so a troop plan item carries its own ``day``). Keyed to the
``king_of_icefield`` event; NOT the power-derived ``state_of_power`` tilt in
``db/event_scoring.yaml``.

Honest about gaps: an activity that doesn't score on the requested day, a troop tier
missing from the (currently empty) ``troop_points`` table, or a troop ``day`` outside the
scoring days is reported in ``unknown`` — never silently counted as zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# games/wos/core/koi/ → parents[2] = games/wos
DEFAULT_KOI_POINTS_PATH = Path(__file__).resolve().parents[2] / "db" / "koi_points.yaml"

TROOP_DAYS = (4, 6)   # troop training/promotion scores on Days 4 and 6 (Combat Training)


@dataclass(frozen=True, slots=True)
class DaySpec:
    """One KoI day: its theme name and the activity → points-per-unit table."""

    day: int
    name: str
    activities: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class KoiPrep:
    """The whole KoI scoring table."""

    days: Mapping[int, DaySpec]
    troop_points: Mapping[int, int]   # tier → points per troop trained (EMPTY: unsourced)


@dataclass(frozen=True, slots=True)
class KoiTroopPlanItem:
    """A planned troop action. ``train`` a fresh tier, or ``promote`` between, on ``day``."""

    action: str                       # "train" | "promote"
    qty: int
    day: int = 4                      # must be a KoI troop day (4 or 6)
    tier: int | None = None           # for train
    from_tier: int | None = None      # for promote
    to_tier: int | None = None        # for promote


@dataclass(frozen=True, slots=True)
class KoiLine:
    """One scored line of a plan."""

    day: int
    activity: str
    qty: int
    unit_points: int
    subtotal: int


@dataclass(frozen=True, slots=True)
class KoiScore:
    """Result of scoring a plan: grand total, per-day split, sorted breakdown."""

    total: int
    per_day: Mapping[int, int]
    breakdown: tuple[KoiLine, ...]
    unknown: tuple[str, ...]          # "<day>:<activity>" entries that don't score


@lru_cache(maxsize=2)
def load_koi_points(path: str | Path | None = None) -> KoiPrep:
    """Load ``koi_points.yaml`` → :class:`KoiPrep` (cached per process)."""
    p = Path(path) if path else DEFAULT_KOI_POINTS_PATH
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
    return KoiPrep(days=days, troop_points=troop)


def points_for(activity: str, day: int, prep: KoiPrep | None = None) -> int | None:
    """Points per unit of ``activity`` on ``day`` (None if it doesn't score that day)."""
    prep = prep or load_koi_points()
    spec = prep.days.get(int(day))
    if spec is None:
        return None
    return spec.activities.get(str(activity))


def days_for(activity: str, prep: KoiPrep | None = None) -> tuple[int, ...]:
    """Which KoI days ``activity`` scores on — for "hold it for its best day" hints."""
    prep = prep or load_koi_points()
    return tuple(
        sorted(day for day, spec in prep.days.items() if str(activity) in spec.activities)
    )


def troop_train_points(tier: int, prep: KoiPrep | None = None) -> int | None:
    """Points for training one fresh troop of ``tier`` (None if tier unknown)."""
    prep = prep or load_koi_points()
    return prep.troop_points.get(int(tier))


def troop_promote_points(
    from_tier: int, to_tier: int, prep: KoiPrep | None = None
) -> int | None:
    """Points for promoting one troop ``from_tier`` → ``to_tier`` (the difference)."""
    prep = prep or load_koi_points()
    lo = prep.troop_points.get(int(from_tier))
    hi = prep.troop_points.get(int(to_tier))
    if lo is None or hi is None:
        return None
    return hi - lo


def _troop_line(item: KoiTroopPlanItem, prep: KoiPrep) -> tuple[KoiLine | None, str | None]:
    """Score one troop action → (line, unknown_label). Exactly one is non-None."""
    day = int(item.day)
    if day not in TROOP_DAYS:
        return None, f"{day}:troop_bad_day"
    if item.action == "train" and item.tier is not None:
        unit = troop_train_points(item.tier, prep)
        label = f"troop_train_t{int(item.tier)}"
    elif item.action == "promote" and item.from_tier is not None and item.to_tier is not None:
        unit = troop_promote_points(item.from_tier, item.to_tier, prep)
        label = f"troop_promote_t{int(item.from_tier)}_t{int(item.to_tier)}"
    else:
        return None, f"{day}:troop_bad_action({item.action})"
    if unit is None:
        return None, f"{day}:{label}"
    qty = int(item.qty)
    return KoiLine(day, label, qty, unit, unit * qty), None


def score_plan(
    plan: Mapping[str | int, Mapping[str, int]] | None,
    troops: Sequence[KoiTroopPlanItem] = (),
    prep: KoiPrep | None = None,
) -> KoiScore:
    """Score a ``{day: {activity: qty}}`` plan (+ optional Day-4/6 troop actions).

    Returns the grand total, the per-day split, a subtotal-sorted breakdown, and the
    ``unknown`` list of ``"<day>:<activity>"`` entries that don't score on that day (or
    troop tiers absent from the table / troop days outside 4 and 6) — surfaced, not
    silently dropped.
    """
    prep = prep or load_koi_points()
    lines: list[KoiLine] = []
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
            lines.append(KoiLine(d, str(activity), q, unit, sub))
            per_day[d] = per_day.get(d, 0) + sub

    for item in troops:
        if int(item.qty) == 0:
            continue
        line, bad = _troop_line(item, prep)
        if line is None:
            unknown.append(bad or f"{int(item.day)}:troop")
            continue
        lines.append(line)
        per_day[line.day] = per_day.get(line.day, 0) + line.subtotal

    lines.sort(key=lambda ln: (-ln.subtotal, ln.day, ln.activity))
    return KoiScore(
        total=sum(per_day.values()),
        per_day=dict(sorted(per_day.items())),
        breakdown=tuple(lines),
        unknown=tuple(sorted(unknown)),
    )
