"""Greedy priority allocator for the shared stamina pool.

Pure decision function: given an estimated stamina level and per-demand runtime
snapshots (window open?, quota left?, reserve active?), pick at most ONE action
this tick — consume for the highest-priority eligible demand, trigger a supply
to refill, or stay idle.

All live-state resolution (OCR reads, event-window detection, quota counters)
happens in the Redis-backed adapter; everything here is deterministic and unit
testable. The full per-demand verdict trace is returned so the dashboard can
answer "why isn't it hunting bandits right now?".
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from .model import Demand, Supply

# --- Per-demand verdict reasons (surfaced in the UI decision trace) ----------
SELECTED = "selected"
WINDOW_CLOSED = "window_closed"          # active_when is false right now
QUOTA_FULL = "quota_full"                # daily quota exhausted
RESERVE_HELD = "reserve_held"            # held back for a higher-priority demand
INSUFFICIENT = "insufficient_stamina"    # est below this demand's cost
NOT_CONSIDERED = "not_considered"        # a higher-priority demand already won

# --- Decision actions --------------------------------------------------------
CONSUME = "consume"
SUPPLY = "supply"
IDLE = "idle"


@dataclass(frozen=True, slots=True)
class DemandRuntime:
    """A :class:`~model.Demand` plus the live state the adapter resolved."""

    demand: Demand
    active: bool                          # window open right now
    quota_used: int = 0
    reserve_active: bool | None = None    # hold reserve_floor? defaults to ``active``

    @property
    def quota_left(self) -> int | None:
        if self.demand.daily_quota is None:
            return None                   # unlimited (overflow sink)
        return max(0, self.demand.daily_quota - self.quota_used)

    @property
    def has_quota(self) -> bool:
        return self.quota_left is None or self.quota_left > 0

    @property
    def reserves(self) -> bool:
        """Whether this demand currently reserves its ``reserve_floor``.

        Defaults to its window state — a demand reserves while active. The
        adapter may set this ``True`` independently to pre-hold stamina for an
        *imminent* window (e.g. Joe event opening soon).
        """
        return self.active if self.reserve_active is None else self.reserve_active


@dataclass(frozen=True, slots=True)
class SupplyRuntime:
    """A :class:`~model.Supply` plus its resolved trigger / quota state."""

    supply: Supply
    triggered: bool
    quota_used: int = 0

    @property
    def quota_left(self) -> int | None:
        if self.supply.daily_quota is None:
            return None
        return max(0, self.supply.daily_quota - self.quota_used)

    @property
    def has_quota(self) -> bool:
        return self.quota_left is None or self.quota_left > 0


@dataclass(frozen=True, slots=True)
class Verdict:
    """Per-demand outcome for one allocation tick (for the UI trace)."""

    demand_id: str
    selected: bool
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Decision:
    """The single action chosen this tick, with the full verdict trace."""

    action: str                           # CONSUME | SUPPLY | IDLE
    reason: str
    task_type: str | None = None
    target_id: str | None = None
    priority: int | None = None
    overflow_pressure: bool = False
    stamina_delta: int = 0                # signed estimate change: -cost / +gives / 0
    verdicts: tuple[Verdict, ...] = ()


def _ranked(demands: Iterable[DemandRuntime]) -> list[DemandRuntime]:
    # Highest priority first; cheaper and stable id as deterministic tie-breaks.
    return sorted(
        demands,
        key=lambda r: (-r.demand.priority, r.demand.cost, r.demand.id),
    )


def allocate(
    est: float | None,
    demands: Sequence[DemandRuntime],
    *,
    cap: int,
    regen_per_hour: float,
    supplies: Sequence[SupplyRuntime] = (),
    hours_to_next_regen: float = 0.0,
) -> Decision:
    """Pick one action for this tick.

    ``est`` is the current stamina estimate (``None`` → treated as 0).
    ``hours_to_next_regen`` lets the adapter signal imminent overflow: when the
    projected level (``est + regen_per_hour * hours``) would exceed ``cap``,
    reserves are dropped so surplus is spent rather than burned.
    """
    s = 0.0 if est is None else float(est)
    overflow = (s + float(regen_per_hour) * float(hours_to_next_regen)) > float(cap)

    ordered = _ranked(demands)
    verdicts: list[Verdict] = []
    winner: DemandRuntime | None = None

    for r in ordered:
        d = r.demand
        if winner is not None:
            verdicts.append(Verdict(d.id, False, NOT_CONSIDERED))
            continue
        if not r.active:
            verdicts.append(Verdict(d.id, False, WINDOW_CLOSED))
            continue
        if not r.has_quota:
            verdicts.append(Verdict(d.id, False, QUOTA_FULL))
            continue
        reserve = sum(
            x.demand.reserve_floor
            for x in ordered
            if x.demand.priority > d.priority and x.reserves
        )
        if not overflow and reserve > 0 and (s - d.cost) < reserve:
            verdicts.append(Verdict(d.id, False, RESERVE_HELD, f"reserve={reserve}"))
            continue
        if s < d.cost:
            verdicts.append(Verdict(d.id, False, INSUFFICIENT, f"need={d.cost}"))
            continue
        verdicts.append(Verdict(d.id, True, SELECTED))
        winner = r

    if winner is not None:
        d = winner.demand
        return Decision(
            action=CONSUME,
            reason=SELECTED,
            task_type=d.task_type,
            target_id=d.id,
            priority=d.priority,
            overflow_pressure=overflow,
            stamina_delta=-d.cost,
            verdicts=tuple(verdicts),
        )

    # No consumer eligible. If a real demand is blocked *only* by low stamina,
    # a triggered supply can refill so it runs next tick.
    blocked = [r for r in ordered if r.active and r.has_quota and s < r.demand.cost]
    if blocked:
        for sup in supplies:
            if sup.triggered and sup.has_quota:
                return Decision(
                    action=SUPPLY,
                    reason="supply_refill",
                    task_type=sup.supply.task_type,
                    target_id=sup.supply.id,
                    overflow_pressure=overflow,
                    stamina_delta=sup.supply.gives,
                    verdicts=tuple(verdicts),
                )

    reasons = {v.reason for v in verdicts}
    if blocked:
        idle_reason = "idle_insufficient_no_supply"
    elif RESERVE_HELD in reasons:
        idle_reason = "idle_reserve_held"
    else:
        idle_reason = "idle_no_eligible_demand"
    return Decision(
        action=IDLE,
        reason=idle_reason,
        overflow_pressure=overflow,
        verdicts=tuple(verdicts),
    )
