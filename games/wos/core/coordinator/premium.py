"""Premium-resource allocators — diamonds, frost stars, and speedups.

Two scarce premium pools, two allocators:

1. **Speedups** — come both *general* (apply to anything) and *type-specific*
   (construction / training / research). The smart rules: spend a type-specific
   speedup on its own activity first and save general ones for where no specific
   one fits; pour them into the *longest* running task (most value); never apply
   more than the task's remaining time (no waste); and respect timing — hoard for
   a points window (the calendar's hold signal) instead of burning early.
2. **Premium currency** (diamonds / frost stars) — many competing sinks (2nd-queue
   rental, recruits, packs, refresh…). Greedy by value-per-spend within the
   balance, like the resource allocator.

Pure: consumes parsed running tasks + inventory + balances (from readers; the
``spend_now`` gate comes from the calendar's hold signal) and returns spend plans.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Speedup categories.
SP_GENERAL = "general"
SP_CONSTRUCTION = "construction"
SP_TRAINING = "training"
SP_RESEARCH = "research"

# Task category → speedup categories it can consume, specific BEFORE general.
TASK_SPEEDUPS: dict[str, tuple[str, ...]] = {
    "construction": (SP_CONSTRUCTION, SP_GENERAL),
    "research": (SP_RESEARCH, SP_GENERAL),
    "training": (SP_TRAINING, SP_GENERAL),
}


@dataclass(frozen=True, slots=True)
class SpeedupTask:
    """A running task that speedups could finish faster."""

    id: str
    category: str            # construction | research | training
    remaining_s: float


@dataclass(frozen=True, slots=True)
class SpeedupApply:
    """Apply ``minutes`` of a ``speedup_category`` speedup to ``task_id``."""

    task_id: str
    speedup_category: str
    minutes: int


@dataclass(frozen=True, slots=True)
class SpeedupPlan:
    applies: tuple[SpeedupApply, ...] = ()
    leftover: Mapping[str, int] = field(default_factory=dict)   # minutes left per category
    reason: str = ""


def recommend_speedups(
    tasks: Sequence[SpeedupTask],
    inventory_minutes: Mapping[str, int],
    *,
    spend_now: bool = True,
) -> SpeedupPlan:
    """Route speedups to the longest tasks: type-specific first, general to fill,
    capped at each task's remaining time. ``spend_now=False`` (a points window is
    imminent) → hold everything for the window."""
    inv = {k: int(v) for k, v in inventory_minutes.items()}
    if not spend_now:
        return SpeedupPlan(applies=(), leftover=inv, reason="hold for points window")

    applies: list[SpeedupApply] = []
    for task in sorted(tasks, key=lambda t: -t.remaining_s):
        remaining_min = max(0, math.ceil(task.remaining_s / 60))
        for cat in TASK_SPEEDUPS.get(task.category, (SP_GENERAL,)):
            if remaining_min <= 0:
                break
            use = min(inv.get(cat, 0), remaining_min)
            if use > 0:
                applies.append(SpeedupApply(task.id, cat, use))
                inv[cat] -= use
                remaining_min -= use
    return SpeedupPlan(applies=tuple(applies), leftover=inv, reason="spend")


# --- premium currency (diamonds / frost stars) -------------------------------


@dataclass(frozen=True, slots=True)
class CurrencySink:
    """One place premium currency can go, with its value and cost."""

    id: str
    currency: str            # "diamonds" | "frost_star" | …
    cost: int
    value: float             # higher = better ROI; the caller sets this
    available: bool = True


@dataclass(frozen=True, slots=True)
class CurrencyPlan:
    spend: tuple[str, ...] = ()                     # sink ids to buy, best-value first
    remaining: int = 0
    skipped: tuple[tuple[str, str], ...] = ()       # (sink id, reason)


def allocate_currency(
    balance: int,
    sinks: Sequence[CurrencySink],
    *,
    currency: str,
) -> CurrencyPlan:
    """Greedily buy the highest-value affordable sinks of ``currency`` within
    ``balance`` (e.g. 2nd-queue rental vs a recruit vs a pack)."""
    remaining = int(balance)
    spend: list[str] = []
    skipped: list[tuple[str, str]] = []
    for sink in sorted((s for s in sinks if s.currency == currency),
                       key=lambda s: (-s.value, s.cost, s.id)):
        if not sink.available:
            skipped.append((sink.id, "unavailable"))
        elif sink.cost <= remaining:
            remaining -= sink.cost
            spend.append(sink.id)
        else:
            skipped.append((sink.id, "insufficient"))
    return CurrencyPlan(spend=tuple(spend), remaining=remaining, skipped=tuple(skipped))
