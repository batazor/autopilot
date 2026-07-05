"""Feedback / self-tuning — learn from outcomes so the bot stops doing dumb things.

The other layers decide from *current* state; this one closes the loop from *past
outcomes*. The executor reports, per dispatched action, whether state actually
advanced (``progressed``). This layer accumulates that into per-action stats and
produces two corrections the coordinator applies:

* **Backoff** — an action that stalls N times in a row (dispatched but nothing
  changed: blocked navigation, a wall, a no-op) gets its priority penalised so the
  bot stops banging on it, and is surfaced as ``stuck`` for the operator. Self-
  healing: one successful progress resets the streak and lifts the penalty.
* **Circuit-breaker** — an action failing repeatedly *for the same reason*
  (``nav_error`` three times in a row: the route is broken, not flaky) is **held**
  entirely for a cooldown window instead of merely deprioritised — retrying can't
  succeed until something else changes. Half-open by expiry: once the window
  passes the action competes again; one success resets the streak.
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
DEFAULT_BREAKER_THRESHOLD = 3     # same-reason failures in a row → hold entirely
DEFAULT_BREAKER_COOLDOWN_S = 3600.0  # how long a tripped breaker holds the action


@dataclass(frozen=True, slots=True)
class Outcome:
    """Result of one dispatched action (reported by the executor)."""

    key: str                      # matches the CandidateAction key
    domain: str
    progressed: bool              # did state actually advance?
    ts: float = 0.0
    reason: str = ""              # why it didn't progress (nav_error / timeout / …)


@dataclass(frozen=True, slots=True)
class ActionStat:
    """Rolling stats for one action key."""

    key: str
    domain: str
    attempts: int = 0
    progressed: int = 0
    consecutive_stalls: int = 0
    last_ts: float = 0.0
    last_reason: str = ""         # reason of the most recent stall ("" after progress)
    same_reason_streak: int = 0   # consecutive stalls sharing last_reason

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
    held: tuple[str, ...] = ()                                   # keys circuit-broken (excluded)


def record(state: FeedbackState, outcome: Outcome) -> FeedbackState:
    """Fold one outcome into the state, returning a new immutable state."""
    prev = state.stats.get(outcome.key)
    if prev is None:
        prev = ActionStat(key=outcome.key, domain=outcome.domain)
    if outcome.progressed:
        reason, reason_streak = "", 0
    else:
        reason = outcome.reason.strip()
        # An empty reason never accumulates a streak — the breaker must only trip
        # on a *diagnosed* repeat failure, not on N anonymous ones.
        same = bool(reason) and reason == prev.last_reason
        reason_streak = prev.same_reason_streak + 1 if same else (1 if reason else 0)
    updated = ActionStat(
        key=outcome.key,
        domain=outcome.domain,
        attempts=prev.attempts + 1,
        progressed=prev.progressed + (1 if outcome.progressed else 0),
        consecutive_stalls=0 if outcome.progressed else prev.consecutive_stalls + 1,
        last_ts=outcome.ts,
        last_reason=reason,
        same_reason_streak=reason_streak,
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
    now: float | None = None,
    breaker_threshold: int = DEFAULT_BREAKER_THRESHOLD,
    breaker_cooldown_s: float = DEFAULT_BREAKER_COOLDOWN_S,
) -> FeedbackBias:
    """Derive backoff penalties for actions stuck ≥ ``stall_threshold`` in a row.

    With ``now``, also trips the circuit-breaker: an action whose last
    ``breaker_threshold`` failures share one diagnosed reason is *held* (excluded
    from planning) until ``breaker_cooldown_s`` after its last attempt. Without
    ``now`` (pure/offline callers) breakers never trip — only soft backoff applies.
    """
    backoff: dict[str, float] = {}
    stuck: list[str] = []
    held: list[str] = []
    for key, st in state.stats.items():
        if (
            now is not None
            and st.same_reason_streak >= breaker_threshold
            and (now - st.last_ts) < breaker_cooldown_s
        ):
            held.append(key)
            continue
        if st.consecutive_stalls >= stall_threshold:
            backoff[key] = backoff_factor
            stuck.append(key)
    return FeedbackBias(backoff=backoff, stuck=tuple(sorted(stuck)), held=tuple(sorted(held)))


def apply_feedback(
    candidates: Sequence[CandidateAction],
    bias: FeedbackBias,
) -> list[CandidateAction]:
    """Drop circuit-broken candidates; penalise the priority of backed-off ones.

    Backoff is self-healing (deprioritised, not removed): scales the candidate's
    :class:`~model.Utility` weight (a post-hoc multiplier on ``total``), so the
    whole utility is backed off and the breakdown stays intact. A ``held`` key is
    excluded outright for the breaker window — same-reason repeat failures mean
    retrying can't help until the cooldown gives the world time to change.
    """
    if not bias.backoff and not bias.held:
        return list(candidates)
    held = set(bias.held)
    out: list[CandidateAction] = []
    for c in candidates:
        if c.key in held:
            continue
        penalty = bias.backoff.get(c.key, 1.0)
        if penalty != 1.0:
            c = replace(c, utility=replace(c.utility, weight=c.utility.weight * penalty))
        out.append(c)
    return out
