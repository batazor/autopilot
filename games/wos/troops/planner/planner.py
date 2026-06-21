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

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

TROOP_TYPES: tuple[str, ...] = ("infantry", "lancer", "marksman")

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
