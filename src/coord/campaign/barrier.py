"""Pure phase-barrier predicate — no Redis, no IO, ``now`` injected.

Decides whether the current phase is READY to advance, still WAITING, or has
TIMED_OUT. The planner applies the phase's ``on_timeout`` policy to a TIMED_OUT.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .model import (
    ALL_REACHED,
    ANY_REACHED,
    DEADLINE_ONLY,
    ELAPSED,
    FLAG_SET,
)

if TYPE_CHECKING:
    from collections.abc import Collection

    from .model import PhaseBarrier

PH_READY = "ready"
PH_WAITING = "waiting"
PH_TIMED_OUT = "timed_out"


def phase_outcome(
    barrier: PhaseBarrier,
    acting_fids: Collection[str],
    reached_fids: Collection[str],
    phase_started_at: float,
    now: float,
) -> str:
    """READY / WAITING / TIMED_OUT for the current phase.

    ``acting_fids`` = the accounts the phase's steps target; ``reached_fids`` =
    those whose barrier signal is currently set.
    """
    acting = set(acting_fids)
    reached = set(reached_fids) & acting
    elapsed = now - phase_started_at
    timed_out = barrier.timeout_s is not None and elapsed >= barrier.timeout_s

    if barrier.kind == DEADLINE_ONLY:
        # The timer IS the gate. No timeout → advance immediately (degenerate).
        ready = barrier.timeout_s is None or elapsed >= barrier.timeout_s
        return PH_READY if ready else PH_WAITING

    if barrier.kind == ELAPSED:
        if elapsed >= barrier.min_dwell_s:
            return PH_READY
        return PH_TIMED_OUT if timed_out else PH_WAITING

    if barrier.kind == ALL_REACHED and acting and reached >= acting:
        return PH_READY
    if barrier.kind in (ANY_REACHED, FLAG_SET) and reached:
        return PH_READY

    return PH_TIMED_OUT if timed_out else PH_WAITING
