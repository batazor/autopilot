"""Value-greedy Intel event planner.

Decides which of the markers on the Intel screen to clear this refresh and in what
order — ranked by loot value (colour × kind), spending ``stamina - reserve`` best-
first under the daily quota. The coordinator adapter turns the batch into MARCH-
channel candidates priced in stamina, lifted above resource gathering so a quick
Intel run precedes a long gather. Pure and testable; live readers are deferred.
"""
from __future__ import annotations

from .model import IntelEvent, from_marker
from .planner import (
    DEFER,
    INSUFFICIENT_STAMINA,
    NONE,
    QUOTA_FULL,
    SELECTED,
    SKIP,
    TAKE,
    IntelCandidate,
    IntelPlan,
    plan_next,
)
from .policy import (
    DEFAULT_COST_PER_EVENT,
    DEFAULT_DAILY_QUOTA,
    MARKER_COLOR_WEIGHT,
    MARKER_KIND_WEIGHT,
    PRIORITY_COLORS,
    intel_value,
)

__all__ = [
    "DEFAULT_COST_PER_EVENT",
    "DEFAULT_DAILY_QUOTA",
    "DEFER",
    "INSUFFICIENT_STAMINA",
    "MARKER_COLOR_WEIGHT",
    "MARKER_KIND_WEIGHT",
    "NONE",
    "PRIORITY_COLORS",
    "QUOTA_FULL",
    "SELECTED",
    "SKIP",
    "TAKE",
    "IntelCandidate",
    "IntelEvent",
    "IntelPlan",
    "from_marker",
    "intel_value",
    "plan_next",
]
