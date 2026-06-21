"""Navigate to an event via the calendar's **Go** button.

Every calendar event's detail popup has a Go button that jumps straight to the
event screen — so the calendar is a universal event-nav hub, reaching events
that have no main_city floating icon (see [[calendar-reading-approach]]).

:func:`navigate_via_go` walks the calendar like the reader does — tap a bar,
read the popup name, dismiss, swipe — but stops at the first popup whose name
matches the requested event and taps its Go button instead of dismissing.

``actions`` is the bot-action surface (``capture_screen_bgr`` / ``tap`` /
``swipe``) and ``ocr`` the ``(crop, *, preprocess)`` callable, both injected.
:func:`name_matches` is pure and unit-tested.
"""
from __future__ import annotations

import asyncio
import logging
import re
from difflib import SequenceMatcher
from typing import Any

from games.wos.core.calendar import capture
from games.wos.core.calendar.parser import (
    OcrFn,
    detect_event_bars,
    find_go_button,
    parse_popup,
)

logger = logging.getLogger(__name__)

DISMISS_POINT = (700, 250)
TAP_SETTLE_MS = 600
SWIPE_SETTLE_MS = 800
MAX_SWIPES = 6
DEFAULT_MATCH_THRESHOLD = 0.72


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def name_matches(name: str, aliases: list[str], threshold: float = DEFAULT_MATCH_THRESHOLD) -> bool:
    """Fuzzy-match an OCR'd popup name against a target's aliases.

    Tolerant of OCR noise and truncation: substring containment (either way) or
    a SequenceMatcher ratio over ``threshold`` counts as a hit.
    """
    n = _norm(name)
    if not n:
        return False
    for alias in aliases:
        a = _norm(alias)
        if not a:
            continue
        if a in n or n in a:
            return True
        if SequenceMatcher(None, a, n).ratio() >= threshold:
            return True
    return False


async def _tap(actions: Any, instance_id: str, point: tuple[int, int], region: str) -> None:
    from layout.types import Point

    await asyncio.to_thread(actions.tap, instance_id, Point(point[0], point[1]), approval_region=region)


async def navigate_via_go(
    actions: Any,
    instance_id: str,
    ocr: OcrFn,
    aliases: list[str],
    *,
    threshold: float = DEFAULT_MATCH_THRESHOLD,
    max_swipes: int = MAX_SWIPES,
    tap_settle_ms: int = TAP_SETTLE_MS,
    swipe_settle_ms: int = SWIPE_SETTLE_MS,
) -> bool:
    """Find the event whose popup name matches ``aliases`` and tap its Go button.

    Returns ``True`` once Go is tapped, ``False`` if no match is found after
    scanning to the bottom. Assumes the caller is already on ``event.calendar``.
    """
    from layout.types import Point

    await capture.scroll_to_top(actions, instance_id)
    frame = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    if frame is None:
        return False
    h, w = frame.shape[:2]
    swipe_from = Point(*[int(round(p / 100.0 * d)) for p, d in zip(capture.SWIPE_FROM_PCT, (w, h), strict=True)])
    swipe_to = Point(*[int(round(p / 100.0 * d)) for p, d in zip(capture.SWIPE_TO_PCT, (w, h), strict=True)])

    for _ in range(max_swipes + 1):
        for point in detect_event_bars(frame):
            await _tap(actions, instance_id, point, "calendar.event_bar")
            await asyncio.sleep(tap_settle_ms / 1000.0)
            popup = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
            event = parse_popup(popup, ocr) if popup is not None else None
            if event is not None and name_matches(event.name, aliases, threshold):
                go = find_go_button(popup)
                if go is not None:
                    await _tap(actions, instance_id, go, "calendar.go")
                    await asyncio.sleep(tap_settle_ms / 1000.0)
                    logger.info("calendar go-nav: matched %r → tapped Go", event.name)
                    return True
            await _tap(actions, instance_id, DISMISS_POINT, "calendar.dismiss")
            await asyncio.sleep(tap_settle_ms / 1000.0)

        await asyncio.to_thread(actions.swipe, instance_id, swipe_from, swipe_to, 400)
        await asyncio.sleep(swipe_settle_ms / 1000.0)
        nxt = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        if nxt is None or capture.reached_bottom(capture.content_delta(frame, nxt)):
            break
        frame = nxt

    logger.info("calendar go-nav: no event matched aliases=%s", aliases)
    return False
