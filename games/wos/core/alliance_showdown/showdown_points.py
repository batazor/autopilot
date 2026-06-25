"""Alliance Showdown points scorer — how many points a planned spend earns.

Alliance Showdown is a ~6.5-day cross-state event of 6 themed stages; each unit of an
activity earns a fixed number of points, and the same activity can score on more than
one stage (Mithril on Stages 4/6, Refined Fire Crystals on Stages 1/5/6, …). The
points-per-unit table is the real game fact wostools.net encodes; we re-encode it in
``games/wos/db/alliance_showdown_points.yaml`` and this module turns a planned
``{stage: {activity: qty}}`` into a points total + per-stage breakdown, plus which
stage(s) an activity scores on (so the operator can hold a resource for its best stage).

Sibling of (but independent from) ``games/wos/core/svs/prep_points.py`` /
``games/wos/core/koi/koi_points.py``. Two structural notes: troop training scores on
Stages 4 AND 6 (the 36h finale repeats every prior activity), so a troop plan item
carries its own ``stage``; and the event's **Baldur** companion adds +5% points per
level (1-6) to all activities — :func:`score_plan` applies it as a per-stage multiplier.

The key extra over the SvS/KoI siblings is :func:`stage_domain_tilt`: every scoring item
maps 1:1 to a coordinator *investment domain* (Refined/Fire Crystal → building, Mithril
→ hero_gear, …), so the per-item point value is an event reward weight. The tilt turns a
stage into a band-relative ``{domain: multiplier}`` map the coordinator merges with its
other event boosts (see ``games/wos/core/coordinator/cycle.py``). Normalisation is
band-relative — exactly the shape of ``building/planner/policy.event_value_bonus`` — so
the flat Baldur bonus cancels out and never changes the relative domain ordering.

Pure: reads the cached YAML, no Redis/device/mutation — safe behind the ``/planner``
calculator. Honest about gaps: an activity that doesn't score on the requested stage, a
troop tier missing from the (currently empty) ``troop_points`` table, or a troop
``stage`` outside the scoring stages is reported in ``unknown`` — never silently 0.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# games/wos/core/alliance_showdown/ → parents[2] = games/wos
DEFAULT_SHOWDOWN_POINTS_PATH = (
    Path(__file__).resolve().parents[2] / "db" / "alliance_showdown_points.yaml"
)

TROOP_STAGES = (4, 6)   # troop training/promotion scores on Stage 4 and the Stage-6 finale

# How much the top-scoring domain of a stage is lifted (band-relative; the stage's best
# investment domain → band × (1 + AS_TILT_WEIGHT), matching the calendar's full boost of
# 1.5 in games/wos/core/coordinator/events.py). Lesser domains scale down linearly.
AS_TILT_WEIGHT = 0.5

# Scoring item → coordinator investment domain (names from
# games/wos/core/coordinator/domains.py). Trucks / speedups / gems / gathering / sigils /
# books are deliberately unmapped: they are not a discrete investment-planner decision.
ITEM_DOMAIN: dict[str, str] = {
    "refined_fire_crystal_building": "building_progression",
    "fire_crystal_building": "building_progression",
    "fc_shard_research": "research",
    "hero_shard_mythic": "heroes",
    "hero_shard_epic": "heroes",
    "hero_shard_rare": "heroes",
    "mithril": "hero_gear",
    "hero_widget": "hero_gear",
    "hero_gear_essence_stone": "hero_gear",
    "wild_mark_advanced": "pets",
    "wild_mark_common": "pets",
    "pet_advancement_score": "pets",
    "chief_charm_score": "charms",
    "chief_gear_score": "gear",
}


@dataclass(frozen=True, slots=True)
class StageSpec:
    """One Alliance Showdown stage: theme, Victory Points, milestone cap, point table."""

    stage: int
    name: str
    activities: Mapping[str, int]
    victory_points: int | None = None     # explicit only for Stages 1/5/6 in the source
    milestone_cap: int = 0


@dataclass(frozen=True, slots=True)
class ShowdownPoints:
    """The whole Alliance Showdown scoring table."""

    stages: Mapping[int, StageSpec]
    troop_points: Mapping[int, int]       # tier → points per troop trained (EMPTY: unsourced)
    baldur_bonus_per_level: float = 0.05
    baldur_max_level: int = 6


@dataclass(frozen=True, slots=True)
class TroopPlanItem:
    """A planned troop action. ``train`` a fresh tier, or ``promote`` between, on ``stage``."""

    action: str                           # "train" | "promote"
    qty: int
    stage: int = 4                        # must be a troop stage (4 or 6)
    tier: int | None = None               # for train
    from_tier: int | None = None          # for promote
    to_tier: int | None = None            # for promote


@dataclass(frozen=True, slots=True)
class ShowdownLine:
    """One scored line of a plan."""

    stage: int
    activity: str
    qty: int
    unit_points: int                      # base points/unit (before the Baldur bonus)
    subtotal: int                         # qty × unit × Baldur multiplier, rounded


@dataclass(frozen=True, slots=True)
class ShowdownScore:
    """Result of scoring a plan: grand total, per-stage split, sorted breakdown."""

    total: int
    per_stage: Mapping[int, int]
    breakdown: tuple[ShowdownLine, ...]
    unknown: tuple[str, ...]              # "<stage>:<activity>" entries that don't score


@lru_cache(maxsize=2)
def load_showdown_points(path: str | Path | None = None) -> ShowdownPoints:
    """Load ``alliance_showdown_points.yaml`` → :class:`ShowdownPoints` (cached per process)."""
    p = Path(path) if path else DEFAULT_SHOWDOWN_POINTS_PATH
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    stages: dict[int, StageSpec] = {}
    for stage, spec in (doc.get("stages") or {}).items():
        s = int(stage)
        spec = spec or {}
        acts = {str(a): int(v) for a, v in (spec.get("activities") or {}).items()}
        vp = spec.get("victory_points")
        stages[s] = StageSpec(
            stage=s,
            name=str(spec.get("name", "")),
            activities=acts,
            victory_points=int(vp) if vp is not None else None,
            milestone_cap=int(spec.get("milestone_cap", 0) or 0),
        )
    troop = {int(t): int(v) for t, v in (doc.get("troop_points") or {}).items()}
    baldur = doc.get("baldur") or {}
    return ShowdownPoints(
        stages=stages,
        troop_points=troop,
        baldur_bonus_per_level=float(baldur.get("bonus_per_level", 0.05)),
        baldur_max_level=int(baldur.get("max_level", 6)),
    )


def points_for(activity: str, stage: int, points: ShowdownPoints | None = None) -> int | None:
    """Base points per unit of ``activity`` on ``stage`` (None if it doesn't score there)."""
    points = points or load_showdown_points()
    spec = points.stages.get(int(stage))
    if spec is None:
        return None
    return spec.activities.get(str(activity))


def stages_for(activity: str, points: ShowdownPoints | None = None) -> tuple[int, ...]:
    """Which stages ``activity`` scores on — for "hold it for its best stage" hints."""
    points = points or load_showdown_points()
    return tuple(
        sorted(s for s, spec in points.stages.items() if str(activity) in spec.activities)
    )


def troop_train_points(tier: int, points: ShowdownPoints | None = None) -> int | None:
    """Points for training one fresh troop of ``tier`` (None if tier unknown)."""
    points = points or load_showdown_points()
    return points.troop_points.get(int(tier))


def troop_promote_points(
    from_tier: int, to_tier: int, points: ShowdownPoints | None = None
) -> int | None:
    """Points for promoting one troop ``from_tier`` → ``to_tier`` (the difference)."""
    points = points or load_showdown_points()
    lo = points.troop_points.get(int(from_tier))
    hi = points.troop_points.get(int(to_tier))
    if lo is None or hi is None:
        return None
    return hi - lo


def _baldur_mult(stage: int, baldur: Mapping[int, int] | None, points: ShowdownPoints) -> float:
    """Multiplier for ``stage`` from the Baldur level (1-6); 1.0 when unset/zero."""
    level = int((baldur or {}).get(int(stage), 0))
    level = max(0, min(level, points.baldur_max_level))
    return 1.0 + points.baldur_bonus_per_level * level


def _troop_line(
    item: TroopPlanItem, baldur: Mapping[int, int] | None, points: ShowdownPoints
) -> tuple[ShowdownLine | None, str | None]:
    """Score one troop action → (line, unknown_label). Exactly one is non-None."""
    stage = int(item.stage)
    if stage not in TROOP_STAGES:
        return None, f"{stage}:troop_bad_stage"
    if item.action == "train" and item.tier is not None:
        unit = troop_train_points(item.tier, points)
        label = f"troop_train_t{int(item.tier)}"
    elif item.action == "promote" and item.from_tier is not None and item.to_tier is not None:
        unit = troop_promote_points(item.from_tier, item.to_tier, points)
        label = f"troop_promote_t{int(item.from_tier)}_t{int(item.to_tier)}"
    else:
        return None, f"{stage}:troop_bad_action({item.action})"
    if unit is None:
        return None, f"{stage}:{label}"
    qty = int(item.qty)
    sub = round(unit * qty * _baldur_mult(stage, baldur, points))
    return ShowdownLine(stage, label, qty, unit, sub), None


def score_plan(
    plan: Mapping[str | int, Mapping[str, int]] | None,
    troops: Sequence[TroopPlanItem] = (),
    baldur: Mapping[int, int] | None = None,
    points: ShowdownPoints | None = None,
) -> ShowdownScore:
    """Score a ``{stage: {activity: qty}}`` plan (+ optional Stage-4/6 troop actions).

    ``baldur`` maps a stage → Baldur level (1-6); each scoring line on that stage is
    lifted +5%/level. Returns the grand total, the per-stage split, a subtotal-sorted
    breakdown, and the ``unknown`` list of ``"<stage>:<activity>"`` entries that don't
    score on that stage (or troop tiers absent from the table / troop stages outside 4
    and 6) — surfaced, not silently dropped.
    """
    points = points or load_showdown_points()
    lines: list[ShowdownLine] = []
    unknown: list[str] = []
    per_stage: dict[int, int] = {}

    for stage, acts in (plan or {}).items():
        s = int(stage)
        mult = _baldur_mult(s, baldur, points)
        for activity, qty in (acts or {}).items():
            q = int(qty)
            if q == 0:
                continue
            unit = points_for(activity, s, points)
            if unit is None:
                unknown.append(f"{s}:{activity}")
                continue
            sub = round(unit * q * mult)
            lines.append(ShowdownLine(s, str(activity), q, unit, sub))
            per_stage[s] = per_stage.get(s, 0) + sub

    for item in troops:
        if int(item.qty) == 0:
            continue
        line, bad = _troop_line(item, baldur, points)
        if line is None:
            unknown.append(bad or f"{int(item.stage)}:troop")
            continue
        lines.append(line)
        per_stage[line.stage] = per_stage.get(line.stage, 0) + line.subtotal

    lines.sort(key=lambda ln: (-ln.subtotal, ln.stage, ln.activity))
    return ShowdownScore(
        total=sum(per_stage.values()),
        per_stage=dict(sorted(per_stage.items())),
        breakdown=tuple(lines),
        unknown=tuple(sorted(unknown)),
    )


def stage_domain_tilt(
    stage: int, points: ShowdownPoints | None = None, *, weight: float = AS_TILT_WEIGHT
) -> dict[str, float]:
    """Band-relative coordinator domain multipliers (≥1.0) for Alliance Showdown ``stage``.

    Each scoring item maps to an investment domain via :data:`ITEM_DOMAIN`; a domain's
    raw weight is the max per-item point value among its items on this stage, normalised
    against the stage's best domain (share-of-best, like ``policy.event_value_bonus``).
    The stage's top domain → ``1 + weight``; lesser domains scale down linearly. Scale-
    invariant, so the flat Baldur bonus does not change the result. Empty if the stage is
    unknown or scores no mapped item.
    """
    points = points or load_showdown_points()
    spec = points.stages.get(int(stage))
    if spec is None:
        return {}
    raw: dict[str, int] = {}
    for activity, pts in spec.activities.items():
        domain = ITEM_DOMAIN.get(activity)
        if domain is None or pts <= 0:
            continue
        raw[domain] = max(raw.get(domain, 0), int(pts))
    if not raw:
        return {}
    best = max(raw.values())
    return {domain: 1.0 + weight * pts / best for domain, pts in raw.items()}
