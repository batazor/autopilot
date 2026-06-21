"""Greedy priority allocator for the shared resource world.

Pure decision function: given a :class:`~model.WorldView` and per-action runtime
snapshots (window open?, quota left?), pick at most ONE action this tick — the
highest-priority one whose *entire* cost vector is affordable — or stay idle.

All live-state resolution (OCR reads, event windows, quota counters, the
reservation ledger) happens in the Redis-backed :mod:`adapter`; everything here
is deterministic and unit testable. The full per-action verdict trace is
returned so the dashboard can answer "why isn't it raiding right now?".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model import (
    SLOT_RESOURCE,
    UNOBSERVED_BLOCKED,
    Action,
    ActionTable,
    Assignment,
    WorldView,
    can_afford,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

# --- Per-action verdict reasons (beyond model's per-cost block reasons) ------
SELECTED = "selected"
WINDOW_CLOSED = "window_closed"      # active_when is false right now
QUOTA_FULL = "quota_full"            # daily quota exhausted
RESERVE_HELD = "reserve_held"        # a slot is held for a higher-priority action
NOT_CONSIDERED = "not_considered"    # a higher-priority action already won

# --- Decision actions --------------------------------------------------------
CONSUME = "consume"
IDLE = "idle"


@dataclass(frozen=True, slots=True)
class ActionRuntime:
    """An :class:`~model.Action` plus the live state the adapter resolved."""

    action: Action
    active: bool                 # window open right now
    quota_used: int = 0

    @property
    def quota_left(self) -> int | None:
        if self.action.daily_quota is None:
            return None
        return max(0, self.action.daily_quota - self.quota_used)

    @property
    def has_quota(self) -> bool:
        return self.quota_left is None or self.quota_left > 0


@dataclass(frozen=True, slots=True)
class Verdict:
    """Per-action outcome for one allocation tick (for the UI trace)."""

    action_id: str
    selected: bool
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    """The single action chosen this tick, with the full verdict trace."""

    action: str                  # CONSUME | IDLE
    reason: str
    task_type: str | None = None
    target_id: str | None = None
    priority: int | None = None
    assignment: Assignment | None = None
    stamina_delta: int = 0       # signed estimate change (−cost on consume)
    slot_cost: int = 0           # march slots this action holds
    lease_seconds: int = 0       # how long the slot/troops/heroes stay held
    verdicts: tuple[Verdict, ...] = ()


def _ranked(runtimes: Sequence[ActionRuntime]) -> list[ActionRuntime]:
    # Highest priority first; stable id as a deterministic tie-break.
    return sorted(runtimes, key=lambda r: (-r.action.priority, r.action.id))


def _reserved_slots_above(
    action: Action, ordered: Sequence[ActionRuntime]
) -> int:
    """Slots held in reserve by *active* higher-priority actions.

    Keeps the last free slot available for, say, a Bear Hunt rally during its
    window instead of letting a low-priority gather consume it first.
    """
    return sum(
        r.action.reserve.get(SLOT_RESOURCE, 0)
        for r in ordered
        if r.action.priority > action.priority and r.active
    )


def allocate(
    world: WorldView,
    runtimes: Sequence[ActionRuntime],
    table: ActionTable,
    *,
    unobserved_policy: str | None = None,
) -> Decision:
    """Pick one action for this tick (or idle)."""
    policy = unobserved_policy or table.unobserved_policy
    ordered = _ranked(runtimes)
    verdicts: list[Verdict] = []
    winner: ActionRuntime | None = None
    winner_afford = None

    for r in ordered:
        a = r.action
        if winner is not None:
            verdicts.append(Verdict(a.id, False, NOT_CONSIDERED))
            continue
        if not r.active:
            verdicts.append(Verdict(a.id, False, WINDOW_CLOSED))
            continue
        if not r.has_quota:
            verdicts.append(Verdict(a.id, False, QUOTA_FULL))
            continue
        reserve = _reserved_slots_above(a, ordered)
        if reserve > 0 and (world.slots_free - a.slot_cost()) < reserve:
            verdicts.append(Verdict(a.id, False, RESERVE_HELD, f"reserve={reserve}"))
            continue
        aff = can_afford(a, world, table, unobserved_policy=policy)
        if not aff.ok:
            b = aff.blocks[0]
            verdicts.append(Verdict(a.id, False, b.reason, b.detail))
            continue
        verdicts.append(Verdict(a.id, True, SELECTED))
        winner = r
        winner_afford = aff

    if winner is not None:
        a = winner.action
        return Decision(
            action=CONSUME,
            reason=SELECTED,
            task_type=a.task_type,
            target_id=a.id,
            priority=a.priority,
            assignment=winner_afford.assignment if winner_afford else None,
            stamina_delta=-a.stamina_cost(),
            slot_cost=a.slot_cost(),
            lease_seconds=a.lease_seconds,
            verdicts=tuple(verdicts),
        )

    return Decision(
        action=IDLE,
        reason=_idle_reason(verdicts),
        verdicts=tuple(verdicts),
    )


def _idle_reason(verdicts: Sequence[Verdict]) -> str:
    reasons = {v.reason for v in verdicts}
    if not reasons or reasons <= {WINDOW_CLOSED, NOT_CONSIDERED}:
        return "idle_no_active_window"
    if RESERVE_HELD in reasons:
        return "idle_reserve_held"
    if UNOBSERVED_BLOCKED in reasons:
        return "idle_unobserved_blocked"
    if reasons <= {QUOTA_FULL, WINDOW_CLOSED, NOT_CONSIDERED}:
        return "idle_quota_full"
    return "idle_blocked_on_resources"
