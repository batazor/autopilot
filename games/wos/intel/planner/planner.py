"""Value-greedy Intel batch planner: which markers to clear, and in what order.

The Intel screen offers a handful of markers each refresh; clearing one costs a
fixed slice of the shared stamina pool. This planner answers the "first analysis"
question — given what's on screen, the current stamina, the day's remaining quota
and a reserve held back for higher-priority stamina consumers (e.g. Crazy Joe):

* rank every worth-taking marker by loot value (colour × kind, see :mod:`policy`),
* spend ``stamina - reserve`` on the best ones first, capped by the daily quota,
* return the ordered ``batch`` to clear now, what's ``DEFER``-red until regen, and
  what was ``SKIP``-ped as too low value.

Pure decision over a marker snapshot — no IO, no cv2. The coordinator adapter
(``core/coordinator/adapters.from_intel_plan``) turns the batch into MARCH-channel
candidates so a quick Intel run is taken before a long resource gather. Live
reading of the markers + stamina is deferred (as for the sibling planners).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .policy import (
    DEFAULT_COST_PER_EVENT,
    PRIORITY_COLORS,
    intel_value,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .model import IntelEvent

# Per-candidate disposition (surfaced in the decision trace / batch).
TAKE = "take"        # clear it this pass (fits stamina budget + quota)
DEFER = "defer"      # worth taking, blocked by stamina or quota right now
SKIP = "skip"        # filtered out (below min_value / not a priority colour)

# Plan reasons
SELECTED = "selected"                       # at least one marker queued
INSUFFICIENT_STAMINA = "insufficient_stamina"   # markers worth taking, none affordable
QUOTA_FULL = "quota_full"                   # daily quota exhausted
NONE = "none"                               # nothing worth taking on screen

_MAX_TRACE = 12


@dataclass(frozen=True, slots=True)
class IntelCandidate:
    """One rated marker, with its disposition for this pass."""

    event: IntelEvent
    value: float
    cost: int
    rank: int            # 1-based value rank among qualifying markers
    status: str          # TAKE | DEFER | SKIP


@dataclass(frozen=True, slots=True)
class IntelPlan:
    """The batch to clear this pass, plus why the rest didn't make the cut."""

    batch: tuple[IntelCandidate, ...]       # status==TAKE, value-ordered
    total_cost: int                          # stamina the batch spends
    reserve: int                             # stamina held back (not for intel)
    stamina_short: int                       # extra stamina to take one more (0 if none)
    reason: str
    candidates: tuple[IntelCandidate, ...] = field(default_factory=tuple)  # full trace

    @property
    def step(self) -> IntelCandidate | None:
        """The next single marker to clear (highest value in the batch)."""
        return self.batch[0] if self.batch else None

    @property
    def deferred(self) -> tuple[IntelCandidate, ...]:
        return tuple(c for c in self.candidates if c.status == DEFER)


def _rank_key(event: IntelEvent, value: float) -> tuple[float, float, int, int]:
    # Highest value first, then confident match, then a stable screen order.
    return (-value, -float(event.score), event.y, event.x)


def plan_next(
    events: Sequence[IntelEvent],
    *,
    stamina: float | None,
    cost_per_event: int = DEFAULT_COST_PER_EVENT,
    reserve: int = 0,
    daily_quota_left: int | None = None,
    min_value: float = 0.0,
    priority_only: bool = False,
    weights: Mapping[str, float] | None = None,
) -> IntelPlan:
    """Pick the value-greedy batch of markers to clear within the stamina budget.

    Args:
      events: detected markers this refresh (image-free :class:`IntelEvent`s).
      stamina: current pool estimate (``None`` → 0).
      cost_per_event: stamina per marker (mirrors ``budget.yaml`` intel_events).
      reserve: stamina to hold back for higher-priority demands (e.g. Joe). Intel
        spends only ``stamina - reserve``.
      daily_quota_left: remaining intel runs today (``None`` → unlimited).
      min_value: drop markers at/below this loot value.
      priority_only: keep only :data:`policy.PRIORITY_COLORS` (gold/purple).
      weights: optional per-colour/kind value overrides.
    """
    cost = max(1, int(cost_per_event))
    have = 0.0 if stamina is None else max(0.0, float(stamina))
    spendable = max(0.0, have - max(0, int(reserve)))
    quota_left = None if daily_quota_left is None else max(0, int(daily_quota_left))

    # Rate + filter, then rank by value.
    rated: list[tuple[IntelEvent, float]] = []
    skipped: list[IntelCandidate] = []
    for ev in events:
        val = intel_value(ev, weights=weights)
        if val <= min_value or (priority_only and ev.color not in PRIORITY_COLORS):
            skipped.append(IntelCandidate(ev, val, cost, rank=0, status=SKIP))
            continue
        rated.append((ev, val))
    rated.sort(key=lambda pair: _rank_key(pair[0], pair[1]))

    batch: list[IntelCandidate] = []
    deferred: list[IntelCandidate] = []
    spent = 0
    taken = 0
    for i, (ev, val) in enumerate(rated):
        rank = i + 1
        quota_ok = quota_left is None or taken < quota_left
        stamina_ok = (spent + cost) <= spendable
        if quota_ok and stamina_ok:
            batch.append(IntelCandidate(ev, val, cost, rank, TAKE))
            spent += cost
            taken += 1
        else:
            deferred.append(IntelCandidate(ev, val, cost, rank, DEFER))

    # How much more stamina would unlock the next worth-taking marker (if it's
    # blocked by stamina rather than quota) — lets a caller set a regen back-off.
    # To clear one more the pool must hold reserve + (taken+1)*cost.
    stamina_short = 0
    quota_exhausted = quota_left is not None and taken >= quota_left
    if deferred and not quota_exhausted:
        stamina_short = max(0, (max(0, int(reserve)) + spent + cost) - int(have))

    if batch:
        reason = SELECTED
    elif not rated:
        reason = NONE
    elif quota_exhausted:
        reason = QUOTA_FULL
    else:
        reason = INSUFFICIENT_STAMINA

    trace = (*batch, *deferred, *skipped)[:_MAX_TRACE]
    return IntelPlan(
        batch=tuple(batch),
        total_cost=spent,
        reserve=min(max(0, int(reserve)), int(have)),
        stamina_short=stamina_short,
        reason=reason,
        candidates=trace,
    )
