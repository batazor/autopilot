"""Feedback / self-tuning — learn from outcomes so the bot stops doing dumb things.

The other layers decide from *current* state; this one closes the loop from *past
outcomes*. The executor reports, per dispatched action, whether state actually
advanced (``progressed``). This layer accumulates that into per-action stats and
produces two corrections the coordinator applies:

* **Backoff** — an action that stalls N times in a row (dispatched but nothing
  changed: blocked navigation, a wall, a no-op) gets its priority penalised so the
  bot stops banging on it, and is surfaced as ``stuck`` for the operator. Self-
  healing: one successful progress resets the streak and lifts the penalty.
* **Success metrics** — per-action success rate + streaks for the dashboard.

Pure and deterministic (no gradient/ML magic, by design): consumes an immutable
:class:`FeedbackState` updated by reported :class:`Outcome`s. The ``progressed``
signal comes from the deferred state readers (compare state before/after).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .model import CandidateAction

DEFAULT_STALL_THRESHOLD = 3       # consecutive no-progress dispatches → back off
DEFAULT_BACKOFF_FACTOR = 0.3      # priority multiplier while stuck


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of one dispatched action (reported by the executor)."""

    key: str                      # matches the CandidateAction key
    domain: str
    progressed: bool              # did state actually advance?
    ts: float = 0.0


@dataclass(frozen=True, slots=True)
class ActionStat:
    """Rolling stats for one action key."""

    key: str
    domain: str
    attempts: int = 0
    progressed: int = 0
    consecutive_stalls: int = 0
    last_ts: float = 0.0

    @property
    def success_rate(self) -> float:
        return self.progressed / self.attempts if self.attempts else 0.0


@dataclass(frozen=True, slots=True)
class FeedbackState:
    """Accumulated per-action history (immutable; updated via :func:`record`)."""

    stats: Mapping[str, ActionStat] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FeedbackBias:
    """Corrections derived from history, applied by the coordinator."""

    backoff: Mapping[str, float] = field(default_factory=dict)   # key → priority mult (<1)
    stuck: tuple[str, ...] = ()                                  # keys backed off (for the trace)


def record(state: FeedbackState, outcome: Outcome) -> FeedbackState:
    """Fold one outcome into the state, returning a new immutable state."""
    prev = state.stats.get(outcome.key)
    if prev is None:
        prev = ActionStat(key=outcome.key, domain=outcome.domain)
    updated = ActionStat(
        key=outcome.key,
        domain=outcome.domain,
        attempts=prev.attempts + 1,
        progressed=prev.progressed + (1 if outcome.progressed else 0),
        consecutive_stalls=0 if outcome.progressed else prev.consecutive_stalls + 1,
        last_ts=outcome.ts,
    )
    return FeedbackState(stats={**state.stats, outcome.key: updated})


def record_many(state: FeedbackState, outcomes: Sequence[Outcome]) -> FeedbackState:
    for o in outcomes:
        state = record(state, o)
    return state


def tuning(
    state: FeedbackState,
    *,
    stall_threshold: int = DEFAULT_STALL_THRESHOLD,
    backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
) -> FeedbackBias:
    """Derive backoff penalties for actions stuck ≥ ``stall_threshold`` in a row."""
    backoff: dict[str, float] = {}
    stuck: list[str] = []
    for key, st in state.stats.items():
        if st.consecutive_stalls >= stall_threshold:
            backoff[key] = backoff_factor
            stuck.append(key)
    return FeedbackBias(backoff=backoff, stuck=tuple(sorted(stuck)))


def apply_feedback(
    candidates: Sequence[CandidateAction],
    bias: FeedbackBias,
) -> list[CandidateAction]:
    """Penalise the priority of backed-off candidates (self-healing — not removed)."""
    if not bias.backoff:
        return list(candidates)
    out: list[CandidateAction] = []
    for c in candidates:
        penalty = bias.backoff.get(c.key, 1.0)
        out.append(replace(c, priority=c.priority * penalty) if penalty != 1.0 else c)
    return out
