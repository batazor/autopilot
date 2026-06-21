"""Read the full event schedule off the calendar screen.

Drives the calendar like a person would: on each visible page, tap every
colored bar, read its detail popup (full name + exact dates), dismiss it; then
swipe down and repeat until the list stops moving. Bars that aren't events
(section dividers, empty grid) open no parseable popup and are silently skipped,
so :func:`~.parser.parse_popup` is the validator — :func:`~.parser.detect_event_bars`
only needs to be over-inclusive (see [[calendar-reading-approach]]).

The caller must already be on ``event.calendar`` (the scenario's ``node``) and
must hold the per-state refresh lock — this is the once-per-state expensive read.

``actions`` is the bot-action surface (``capture_screen_bgr`` / ``tap`` / ``swipe``)
and ``ocr`` the ``(crop, *, preprocess)`` callable, both injected so the loop is
swappable; :func:`dedup_events` is pure and unit-tested.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from games.wos.core.calendar import capture
from games.wos.core.calendar.parser import (
    OcrFn,
    PopupEvent,
    detect_event_bars,
    parse_popup,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# A point off the popup card (top-right, below the tab strip) — tapping the dim
# backdrop there dismisses the detail popup without hitting another control.
DISMISS_POINT = (700, 250)
TAP_SETTLE_MS = 600         # popup open / dismiss animation
SWIPE_SETTLE_MS = 800
MAX_SWIPES = 6              # bottom is ~2-3 swipes; cap for safety


def dedup_events(events: Sequence[PopupEvent]) -> list[PopupEvent]:
    """Collapse repeats (same bar seen across overlapping scroll pages).

    Keyed by ``(name, starts_at)`` so a recurring event's distinct occurrences
    are kept; order follows first sighting (top-to-bottom, earliest page).
    """
    out: dict[tuple[str, str], PopupEvent] = {}
    for ev in events:
        key = (ev.name, ev.starts_at.isoformat())
        out.setdefault(key, ev)
    return list(out.values())


async def _tap(actions: Any, instance_id: str, point: tuple[int, int], region: str) -> None:
    from layout.types import Point

    await asyncio.to_thread(
        actions.tap, instance_id, Point(point[0], point[1]), approval_region=region
    )


async def scan_calendar(
    actions: Any,
    instance_id: str,
    ocr: OcrFn,
    *,
    max_swipes: int = MAX_SWIPES,
    tap_settle_ms: int = TAP_SETTLE_MS,
    swipe_settle_ms: int = SWIPE_SETTLE_MS,
) -> list[PopupEvent]:
    """Walk the calendar top-to-bottom, returning every event read (deduped)."""
    from layout.types import Point

    events: list[PopupEvent] = []
    await capture.scroll_to_top(actions, instance_id)
    frame = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    if frame is None:
        return []
    h, w = frame.shape[:2]
    swipe_from = Point(*[int(round(p / 100.0 * d)) for p, d in zip(capture.SWIPE_FROM_PCT, (w, h), strict=True)])
    swipe_to = Point(*[int(round(p / 100.0 * d)) for p, d in zip(capture.SWIPE_TO_PCT, (w, h), strict=True)])

    for _ in range(max_swipes + 1):
        for point in detect_event_bars(frame):
            await _tap(actions, instance_id, point, "calendar.event_bar")
            await asyncio.sleep(tap_settle_ms / 1000.0)
            popup = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
            event = parse_popup(popup, ocr) if popup is not None else None
            if event is not None:
                events.append(event)
            await _tap(actions, instance_id, DISMISS_POINT, "calendar.dismiss")
            await asyncio.sleep(tap_settle_ms / 1000.0)

        await asyncio.to_thread(actions.swipe, instance_id, swipe_from, swipe_to, 400)
        await asyncio.sleep(swipe_settle_ms / 1000.0)
        nxt = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        if nxt is None or capture.reached_bottom(capture.content_delta(frame, nxt)):
            break
        frame = nxt

    deduped = dedup_events(events)
    logger.info(
        "calendar scan: instance=%s read=%d unique=%d", instance_id, len(events), len(deduped)
    )
    return deduped
