"""Daily-task awareness — finish the daily checklist for the reward chests.

Daily Missions award activity points (→ chests) for doing a set of activities, and
reset every day. Most are knocked out by normal play, but a smart bot (a) leans
into the domains whose daily task is still open — harder as reset approaches so
nothing is left on the table, (b) nudges the cheap one-shots that the main loop
won't naturally cover (a free recruit, an alliance help, spending the daily
stamina), and (c) claims whatever is already complete.

Pure: consumes parsed :class:`DailyTask`s (from a reader) + time-to-reset, returns
a :class:`DailyBias` — a domain-boost map (threaded into the coordinator like the
calendar bias), plus nudge + claim directives the executor runs. Categories map to
coordinator domains via the table below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Daily-task category → the coordinator domains that complete it.
CATEGORY_DOMAINS: dict[str, tuple[str, ...]] = {
    "build": ("building_progression", "building_economy"),
    "research": ("research",),
    "train": ("troops", "building_camp"),
    "gather": ("gather",),
    "stamina": ("raids",),          # spending stamina = beast/intel raids
    "hero": ("heroes",),
    "pet": ("pets",),
}

# Cheap one-shots the main planning loop won't naturally trigger → nudge them.
ONE_SHOT_CATEGORIES: frozenset[str] = frozenset({"recruit", "help", "vip", "gems", "explore"})

DEFAULT_BOOST = 1.3
DEFAULT_URGENT_MULT = 1.5         # within the urgency horizon, push harder
DEFAULT_URGENCY_HORIZON_S = 4 * 3600


@dataclass(frozen=True, slots=True)
class DailyTask:
    """One daily mission (from the reader)."""

    id: str
    category: str
    target: int = 1
    progress: int = 0
    claimable: bool = False        # completed and reward unclaimed

    @property
    def done(self) -> bool:
        return self.progress >= self.target


@dataclass(frozen=True, slots=True)
class DailyNudge:
    """A cheap one-shot activity to do for an open daily task."""

    task_id: str
    category: str
    reason: str = ""


@dataclass(frozen=True, slots=True)
class DailyBias:
    """Coordinator inputs derived from the daily checklist."""

    domain_boost: Mapping[str, float] = field(default_factory=dict)
    nudges: tuple[DailyNudge, ...] = ()
    claims: tuple[str, ...] = ()   # task ids whose reward to claim now


def daily_bias(
    tasks: Sequence[DailyTask],
    *,
    seconds_to_reset: float | None = None,
    boost_base: float = DEFAULT_BOOST,
    urgent_mult: float = DEFAULT_URGENT_MULT,
    urgency_horizon_s: float = DEFAULT_URGENCY_HORIZON_S,
) -> DailyBias:
    """Boost domains with open daily tasks (harder near reset), nudge one-shots,
    and collect claimable rewards."""
    near_reset = seconds_to_reset is not None and seconds_to_reset <= urgency_horizon_s
    factor = boost_base * (urgent_mult if near_reset else 1.0)

    domain_boost: dict[str, float] = {}
    nudges: list[DailyNudge] = []
    claims: list[str] = []

    for task in tasks:
        if task.claimable:
            claims.append(task.id)
        if task.done:
            continue
        for domain in CATEGORY_DOMAINS.get(task.category, ()):
            domain_boost[domain] = max(domain_boost.get(domain, 1.0), factor)
        if task.category in ONE_SHOT_CATEGORIES:
            nudges.append(DailyNudge(task.id, task.category, f"daily {task.category} not done"))

    return DailyBias(domain_boost=domain_boost, nudges=tuple(nudges), claims=tuple(claims))


def merge_boosts(*maps: Mapping[str, float]) -> dict[str, float]:
    """Combine boost maps (calendar + daily + …) by taking the max per domain."""
    out: dict[str, float] = {}
    for m in maps:
        for domain, value in m.items():
            out[domain] = max(out.get(domain, 1.0), value)
    return out
