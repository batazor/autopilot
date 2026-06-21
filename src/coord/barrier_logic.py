"""Pure barrier state machine — no Redis, no ``time.time()`` inside.

``now`` is injected so the transition table is deterministic and golden-testable
exactly like ``scheduler.ranking.compute_rank``. The Redis wrapper
(:mod:`coord.barrier`) persists arrivals and calls :func:`evaluate` to decide
when a phase may advance.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .models import (
    BARRIER_ABORTED,
    BARRIER_READY,
    BARRIER_TIMED_OUT,
    BARRIER_WAITING,
)

if TYPE_CHECKING:
    from collections.abc import Collection

    from .models import BarrierSpec


def evaluate(
    spec: BarrierSpec,
    arrived: Collection[str],
    now: float,
    *,
    aborted: bool = False,
) -> str:
    """Return the barrier outcome.

    Precedence: an explicit abort wins; then quorum (READY) — a met quorum is
    READY even past its deadline; then the deadline (TIMED_OUT); else WAITING.
    """
    if aborted:
        return BARRIER_ABORTED
    if len(set(arrived)) >= spec.required_n:
        return BARRIER_READY
    if now >= spec.deadline_ts:
        return BARRIER_TIMED_OUT
    return BARRIER_WAITING
