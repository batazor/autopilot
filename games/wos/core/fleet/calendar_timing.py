"""Calendar anticipation — when to pre-position for an upcoming event window.

Pure. A campaign that acts during an event window often needs lead time to be
ready at open (recall gatherers home, finish a gather cycle, position troops).
Given the campaign's anchor :class:`~coordinator.EventWindow` (or None) and its
prep lead, classify the moment as ``idle`` / ``prep`` (start preparing now) /
``active`` / ``closing`` (window about to shut — rush what's left).

The orchestrator uses ``prep_now`` to spin up prep ahead of the window and
``closing`` to prioritise finishing. Window plumbing (``build_calendar_view``)
and the prep scenarios are deferred — this is the pure timing brain.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

IDLE = "idle"
PREP = "prep"
ACTIVE = "active"
CLOSING = "closing"

# How early to begin prep before a window opens (seconds).
PREP_LEAD: dict[str, float] = {
    "joint_event": 1800.0,   # 30 min to recall gatherers / position
    "farm_raid": 600.0,
    "reinforcement": 0.0,    # reactive — no prep
}
DEFAULT_PREP_LEAD = 0.0
CLOSING_THRESHOLD_S = 600.0  # last 10 min of an active window counts as "closing"


@dataclass(frozen=True, slots=True)
class Timing:
    phase: str            # idle | prep | active | closing
    starts_in_s: float
    ends_in_s: float
    prep_now: bool


def prep_lead_for(campaign_id: str, *, leads: Mapping[str, float] = PREP_LEAD) -> float:
    return float(leads.get(campaign_id, DEFAULT_PREP_LEAD))


def campaign_timing(
    window: Any,
    *,
    prep_lead_s: float,
    closing_threshold_s: float = CLOSING_THRESHOLD_S,
) -> Timing:
    """Classify a campaign's moment from its anchor window (``EventWindow`` or None).

    ``window`` is duck-typed (``.active`` / ``.starts_in_s`` / ``.ends_in_s``) so
    this stays decoupled from the calendar package.
    """
    if window is None:
        return Timing(IDLE, float("inf"), float("inf"), False)
    if window.active:
        phase = CLOSING if window.ends_in_s <= closing_threshold_s else ACTIVE
        return Timing(phase, 0.0, float(window.ends_in_s), False)
    prep_now = window.starts_in_s <= prep_lead_s
    return Timing(
        PREP if prep_now else IDLE,
        float(window.starts_in_s),
        float(window.ends_in_s),
        prep_now,
    )
