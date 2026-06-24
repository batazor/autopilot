"""Troop-training planner — which troop type to train next.

The three camps (infantry / lancer / marksman) are all the *battle* category, so
an account role doesn't tell them apart; what does is the desired ARMY
COMPOSITION. The planner ranks types by how far each sits BELOW its target share:
train the most-deficient first, driving the army toward the target ratio.

Until the troop-pool reader lands (counts per type), ``counts`` is ``None`` and
the planner falls back to the static meta order (the target weights). It's pure
so the ranking is unit-testable; the driver scenario consumes
:func:`plan_training` to pick the best IDLE camp.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from games.wos.core.resources.troop_stats import load_troop_stats

from .training_costs import TrainTier, promote_cost_time, tier_cost_time

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

TROOP_TYPES: tuple[str, ...] = ("infantry", "lancer", "marksman")
MAX_TIER = 11                     # T1 (Rookie) … T11 (Helios)

# Target army composition (shares sum ~1.0). Infantry leads as the front line;
# lancer/marksman split the rest. Meta — override per role/season when desired.
DEFAULT_TARGET: dict[str, float] = {"infantry": 0.34, "lancer": 0.33, "marksman": 0.33}


def _shares(counts: Mapping[str, int]) -> dict[str, float]:
    total = sum(max(0, int(counts.get(t, 0))) for t in TROOP_TYPES)
    if total <= 0:
        return dict.fromkeys(TROOP_TYPES, 0.0)
    return {t: max(0, int(counts.get(t, 0))) / total for t in TROOP_TYPES}


def rank_troops(
    counts: Mapping[str, int] | None = None,
    target: Mapping[str, float] | None = None,
) -> tuple[str, ...]:
    """Troop types most-wanted first.

    With ``counts``: by largest deficit (``target_share - actual_share``) so the
    army converges on ``target``. Without: by descending target weight (the meta
    default order). Ties break toward the :data:`TROOP_TYPES` order.
    """
    tgt = target or DEFAULT_TARGET
    share = _shares(counts) if counts is not None else None

    def _key(t: str) -> tuple[float, int]:
        want = tgt.get(t, 0.0) if share is None else tgt.get(t, 0.0) - share.get(t, 0.0)
        return (-want, TROOP_TYPES.index(t))

    return tuple(sorted(TROOP_TYPES, key=_key))


def plan_training(
    idle: Iterable[str],
    counts: Mapping[str, int] | None = None,
    target: Mapping[str, float] | None = None,
) -> str | None:
    """The highest-priority troop type among the ``idle`` camps, or ``None`` when
    none are idle."""
    idle_set = {str(t) for t in idle}
    for troop in rank_troops(counts, target):
        if troop in idle_set:
            return troop
    return None


# --- Value-greedy pick: which (type, tier) to train next ---------------------
SELECTED = "selected"
NONE = "none"                     # nothing trainable (every camp tier-capped at 0)

TRAIN = "train"                   # fresh troops
PROMOTE = "promote"               # raise existing tier-1 troops → tier (pays the diff)


@dataclass(frozen=True, slots=True)
class TrainCandidate:
    """One trainable troop: the best tier of a camp, with its per-unit power + cost."""

    troop_type: str           # infantry | lancer | marksman
    tier: int                 # the tier we'd train (highest unlocked ≤ cap)
    fc: int                   # Fire-Crystal level the stat lookup used
    name: str                 # tier name (Rookie … Helios)
    power: int                # per-unit power — the value of training this unit
    deficit: float            # target_share − actual_share (how under-target, for trace)
    kind: str = TRAIN         # train (fresh) | promote (raise lower-tier troops)
    batch: int = 1            # troops this candidate covers (cost/time scale with it)
    cost: Mapping[str, int] = field(default_factory=dict)   # meat/wood/coal/iron (empty if no data)
    time_s: int = 0           # training time for the batch (0 if no data)


@dataclass(frozen=True, slots=True)
class TrainingPlan:
    """What to train now: the best camp pick plus the ranked trace."""

    step: TrainCandidate | None
    reason: str
    candidates: tuple[TrainCandidate, ...] = field(default_factory=tuple)


def _cap_for(value: Any, troop_type: str, default: int) -> int:
    """Resolve a per-type or scalar cap/level for ``troop_type``."""
    get = getattr(value, "get", None)
    return int(get(troop_type, default)) if callable(get) else int(value)


def plan_next(
    counts: Mapping[str, int] | None = None,
    *,
    max_tier: int | Mapping[str, int] = MAX_TIER,
    fc: int | Mapping[str, int] = 0,
    target: Mapping[str, float] | None = None,
    batch: int = 1,
    stats: Mapping[tuple[str, int, int], Any] | None = None,
    costs: Mapping[int, TrainTier] | None = None,
) -> TrainingPlan:
    """Pick the next troop to train: the most-deficient type at its highest tier.

    Composition drives *which* type (reusing :func:`rank_troops`); within a camp we
    always train the highest unlocked tier (``max_tier`` — a per-type or scalar cap
    set by the camp level / research, since troop tiers aren't on the server-age
    clock). The candidate's value is the per-unit ``power`` from the troop-stats data;
    its ``cost``/``time_s`` (for ``batch`` troops) come from the training table — empty
    until that table is filled. When the chosen tier has data, a cheaper ``promote``
    sibling (raise tier-1 troops, paying the diff) is appended for the trace. A type
    whose ``max_tier`` is <1 (camp not built) is skipped. Pure — ``counts`` may be
    ``None`` (meta order).
    """
    table = stats if stats is not None else load_troop_stats()
    cost_table = costs if costs is not None else None   # None → loaders use the default file
    shares = _shares(counts) if counts is not None else None
    tgt = target or DEFAULT_TARGET

    candidates: list[TrainCandidate] = []
    for troop in rank_troops(counts, target):          # already deficit / meta order
        cap = _cap_for(max_tier, troop, MAX_TIER)
        if cap < 1:
            continue                                   # camp can't train anything yet
        tier = min(cap, MAX_TIER)
        fc_lvl = max(0, _cap_for(fc, troop, 0))
        stat = table.get((troop, tier, fc_lvl))
        if stat is None:
            continue
        deficit = tgt.get(troop, 0.0) - (shares.get(troop, 0.0) if shares else 0.0)
        cost, time_s = tier_cost_time(tier, batch=batch, table=cost_table)
        candidates.append(TrainCandidate(
            troop_type=troop, tier=tier, fc=fc_lvl,
            name=getattr(stat, "name", ""), power=int(getattr(stat, "power", 0)),
            deficit=deficit, kind=TRAIN, batch=batch, cost=cost, time_s=time_s,
        ))

    step = candidates[0] if candidates else None       # most-deficient trainable camp
    if step is not None and step.tier > 1:             # surface the cheaper promote path
        pcost, ptime = promote_cost_time(step.tier, batch=batch, table=cost_table)
        if pcost:
            candidates.append(replace(step, kind=PROMOTE, cost=pcost, time_s=ptime))
    return TrainingPlan(step, SELECTED if step else NONE, tuple(candidates))
