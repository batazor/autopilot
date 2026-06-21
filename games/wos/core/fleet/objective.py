"""Campaign priority for fleet arbitration — the WoS objective.

Pure. Mirrors ``coordinator.objective.domain_priority`` one level up: a per-campaign
base band, lifted by **urgency** (rises as a run nears its deadline / an event
window closes) and an **incumbency** bonus (a run already in progress resists
preemption, so we don't thrash a half-done raid). The arbiter
(``coord.campaign.arbitrate``) then packs the highest-priority conflict-free set.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coord.campaign import CampaignDef, CampaignRun

# Base priority bands (coordinator DOMAIN_BAND scale, ~hundreds).
BAND: dict[str, float] = {
    "reinforcement": 950.0,   # reactive, time-critical — an ally is under attack
    "joint_event": 600.0,     # cooperative, scheduled
    "farm_raid": 500.0,       # opportunistic
}
DEFAULT_BAND = 400.0

# Urgency lifts priority up to this multiple as a run approaches its deadline.
URGENCY_MAX = 2.0
# A run past phase 0 resists preemption (avoid thrashing a half-done campaign).
INCUMBENCY_BONUS = 1.25


def urgency(run: CampaignRun, now: float) -> float:
    """1.0 at the start of the run's life, rising linearly to ``URGENCY_MAX`` at
    its deadline (so a closing event window / imminent reinforcement outranks a
    fresh opportunistic raid)."""
    span = run.deadline_at - run.started_at
    if span <= 0:
        return URGENCY_MAX
    remaining = run.deadline_at - now
    frac_elapsed = 1.0 - max(0.0, min(1.0, remaining / span))
    return 1.0 + frac_elapsed * (URGENCY_MAX - 1.0)


# Raid ROI lifts a raid's priority up to this multiple (a fat farm outranks a
# marginal one when they contend for the same fighter).
VALUE_FACTOR_CAP = 2.0
VALUE_FACTOR_SCALE = 1000.0


def raid_value_factor(
    roi: float, *, scale: float = VALUE_FACTOR_SCALE, cap: float = VALUE_FACTOR_CAP
) -> float:
    """Map a raid ROI to a priority multiplier in ``[1.0, cap]`` (the orchestrator
    passes this as ``value_factor`` once the troop/resource readers exist)."""
    if roi <= 0:
        return 1.0
    return 1.0 + min(cap - 1.0, roi / scale)


def campaign_priority(
    cdef: CampaignDef, run: CampaignRun, now: float, *, value_factor: float = 1.0
) -> float:
    """Cross-campaign priority of a run this tick (higher = wins contention).

    ``value_factor`` (e.g. from :func:`raid_value_factor`) lets a high-ROI raid
    outrank a marginal one — the economics feeding the arbiter."""
    pri = BAND.get(cdef.id, DEFAULT_BAND) * urgency(run, now)
    if run.phase_index > 0:
        pri *= INCUMBENCY_BONUS
    return pri * max(0.0, value_factor)
