"""Should the bot rent the locked 2nd construction queue for diamonds?

A player may have only 1 build queue; the 2nd is locked but rentable for a fixed
diamond cost (≈4000) for a time window. Renting only pays off when that queue
would be well-utilised during the window — the user's two cases:

* Early game: many quick builds queued up → the 2nd queue churns through several
  → high utilisation, worth it.
* Late game: a single multi-day build to run in parallel → worth it even though
  it's "one" build (a long build alone fills the window).

The dead middle — a lone medium build with nothing behind it — leaves the rented
queue mostly idle, so don't burn diamonds. This is a pure ROI gate over the
:class:`~planner.BuildSlate` the planner already produces (the construction queue
is a slot resource; renting temporarily lifts its capacity 1 → 2).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .planner import BuildCandidate, BuildSlate

DEFAULT_RENTAL_COST = 4000          # diamonds
DEFAULT_RENTAL_WINDOW_S = 24 * 3600  # rental duration (configurable)
DEFAULT_MIN_UTILISATION = 0.6        # window fraction the queue must stay busy
DEFAULT_LONG_BUILD_S = 12 * 3600     # a single build this long alone justifies it

# Decision reasons
ALREADY_OPEN = "already_open"            # 2nd queue not locked → no rental needed
NO_WORK = "no_work"                      # nothing for the 2nd queue to build
LOW_VALUE = "low_value"                  # would sit idle most of the window
INSUFFICIENT_DIAMONDS = "insufficient_diamonds"
WORTH_IT = "worth_it"


@dataclass(frozen=True, slots=True)
class QueueRentalDecision:
    rent: bool
    reason: str
    projected_build_s: int = 0       # work the 2nd queue would do within the window
    utilisation: float = 0.0         # projected / window
    candidate: BuildCandidate | None = None   # first build it would start


def evaluate_queue_rental(
    slate: BuildSlate,
    *,
    second_queue_locked: bool,
    diamonds: int,
    rental_cost: int = DEFAULT_RENTAL_COST,
    rental_window_s: int = DEFAULT_RENTAL_WINDOW_S,
    min_utilisation: float = DEFAULT_MIN_UTILISATION,
    long_build_s: int = DEFAULT_LONG_BUILD_S,
) -> QueueRentalDecision:
    """Decide whether renting the 2nd queue is worth ``rental_cost`` diamonds now.

    ``slate`` should be planned as if 2 queues were free (so its candidate list
    holds what the 2nd queue could build). Worth it when the queue would stay busy
    for ``min_utilisation`` of the window, OR a single build ≥ ``long_build_s`` can
    run in parallel — and only if ``diamonds`` covers the cost.
    """
    if not second_queue_locked:
        return QueueRentalDecision(False, ALREADY_OPEN)

    # What the 2nd queue could run = affordable candidates beyond queue 1's pick.
    first_id = slate.picks[0].instance_id if slate.picks else None
    pool = [c for c in slate.candidates if c.affordable and c.instance_id != first_id]
    if not pool:
        return QueueRentalDecision(False, NO_WORK)

    projected = 0
    for c in pool:
        if projected >= rental_window_s:
            break
        projected += max(0, c.time_s)
    projected = min(projected, rental_window_s)
    utilisation = projected / rental_window_s if rental_window_s > 0 else 0.0
    has_long_build = any(c.time_s >= long_build_s for c in pool)

    if utilisation < min_utilisation and not has_long_build:
        return QueueRentalDecision(False, LOW_VALUE, projected, utilisation, pool[0])
    if diamonds < rental_cost:
        return QueueRentalDecision(False, INSUFFICIENT_DIAMONDS, projected, utilisation, pool[0])
    return QueueRentalDecision(True, WORTH_IT, projected, utilisation, pool[0])
