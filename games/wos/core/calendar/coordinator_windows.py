"""Bridge the calendar schedule → coordinator ``EventWindow``s.

The coordinator's :func:`coordinator.calendar_bias` plays the schedule — it lifts
the domains a live points event rewards and emits hoard/spend holds — but it
consumes :class:`coordinator.EventWindow` (slug + active + start/end offsets),
while the calendar stores concrete ``(name, start, end)`` occurrences. This is the
missing converter between the two, so the read schedule actually drives the
coordinator instead of only raising ``event_<slug>`` flags.

Pure: occurrences + ``now`` in, ``EventWindow``s out. The slug matches the
calendar's ``event_<slug>`` flag and the coordinator's ``EVENT_CATALOG`` keys
(``slug("Power Up") == "power_up"``); unknown slugs are harmless — ``calendar_bias``
ignores any it doesn't recognise. ``phase_category`` is left ``None`` until an
in-event phase reader exists (``calendar_bias`` then applies a softer provisional
boost for phased events).
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from games.wos.core.coordinator import EventWindow

from .schedule import slug

if TYPE_CHECKING:
    from typing import Any

    from .schedule import ScheduleEvent


def _better(candidate: EventWindow, current: EventWindow) -> bool:
    """Which of two windows for the same slug to keep: a live one beats an
    upcoming one; among the same state, the sooner-starting wins."""
    if candidate.active != current.active:
        return candidate.active
    return candidate.starts_in_s < current.starts_in_s


def event_windows(events: list[ScheduleEvent], now: datetime) -> list[EventWindow]:
    """Convert calendar occurrences to coordinator ``EventWindow``s.

    Drops fully-past occurrences; for an event with several upcoming occurrences
    (or a past + live pair) keeps the most relevant one (live, else soonest).
    """
    best: dict[str, EventWindow] = {}
    for name, start, end in events:
        s = slug(name)
        if not s or end <= now:                      # no usable slug / already over
            continue
        active = start <= now < end
        window = EventWindow(
            slug=s,
            active=active,
            starts_in_s=max(0.0, (start - now).total_seconds()) if start > now else 0.0,
            ends_in_s=max(0.0, (end - now).total_seconds()) if active else 0.0,
        )
        prev = best.get(s)
        if prev is None or _better(window, prev):
            best[s] = window
    return list(best.values())


def event_windows_from_digest(digest: list[dict[str, Any]], now: datetime) -> list[EventWindow]:
    """Build windows from a stored schedule digest (the shape cached per state).

    Mirrors :func:`schedule.flags_from_digest` — lets the live per-player path
    derive windows from the cached schedule between calendar reads, no DB hit.
    """
    events: list[ScheduleEvent] = []
    for bucket in digest:
        for row in bucket.get("events", []):
            try:
                start = datetime.fromisoformat(row["start"])
                end = datetime.fromisoformat(row["end"])
            except (KeyError, TypeError, ValueError):
                continue
            events.append((str(row.get("name", "")), start, end))
    return event_windows(events, now)
