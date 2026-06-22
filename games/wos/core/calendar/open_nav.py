"""Open the Events panel and select the Calendar tab.

The Events panel is a horizontal, swipe-only carousel of event tabs that opens on
whatever event is currently *primary* (the truck trade, Alliance Showdown, …), so
Calendar is usually scrolled off to the LEFT. The node graph can't reach it: the
tab strip doesn't segment cleanly and the per-event icons aren't discriminative
(see ``games/wos/events/state_of_power/exec.py`` for the same problem).

Calendar is the one tab we can reach structurally rather than by recognition: it is
**always the first (leftmost) tab**. So this just swipes the carousel to its
leftmost page (detected by the strip image going still) and taps slot 0 — no OCR
discrimination needed. Mirrors the proven ``goto_state_of_power`` swipe loop.

Reached as the ``goto_calendar`` dynamic edge resolver from
``routes/edge_taps.yaml`` (``main_city → event.calendar``); the navigator verifies
``event.calendar`` after this returns.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import cv2

logger = logging.getLogger(__name__)

# Frames come back at the emulator's mandatory 720x1280.
_W, _H = 720, 1280

# Tap targets (x, y) fractions.
_EVENTS_BUTTON = (0.932, 0.110)   # the fixed events.button on main_city (~671,141)
_CALENDAR_TAB = (0.185, 0.112)    # leftmost Calendar tab (calendar.tab bbox center)

# Strip window (x, y, w, h) fractions — the tab-icon row, for the stillness check.
_STRIP_BAND = (0.0, 0.045, 1.0, 0.10)

_MAX_SWIPE = 6           # swipes to reach the leftmost page (carousel is small)
_SWIPE_Y = 0.09          # swipe along the tab row
_STRIP_MOVE_EPS = 4.0    # mean abs gray diff above which a swipe "moved" the strip


def _crop(frame: Any, win: tuple[float, float, float, float]) -> Any:
    x, y, w, h = win
    fh, fw = frame.shape[:2]
    return frame[int(y * fh):int((y + h) * fh), int(x * fw):int((x + w) * fw)]


def _strip_sig(frame: Any) -> Any:
    patch = _crop(frame, _STRIP_BAND)
    if patch is None or patch.size == 0:
        return None
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    return cv2.resize(gray, (120, 16), interpolation=cv2.INTER_AREA)


def _moved(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return True
    return float(cv2.absdiff(a, b).mean()) > _STRIP_MOVE_EPS


async def _tap(actions: Any, instance_id: str, frac: tuple[float, float], label: str) -> None:
    from layout.types import Point

    x, y = frac
    pt = Point(int(x * _W), int(y * _H))
    try:
        await asyncio.to_thread(actions.tap, instance_id, pt, approval_region=label)
    except Exception:
        logger.debug("goto_calendar: tap %s failed", label, exc_info=True)


async def _swipe(actions: Any, instance_id: str, *, forward: bool) -> None:
    """forward=True drags right->left (reveal right tabs); forward=False drags
    left->right (reveal left / toward the start)."""
    from layout.types import Point

    y = int(_SWIPE_Y * _H)
    lo, hi = int(0.20 * _W), int(0.80 * _W)
    start, end = (Point(hi, y), Point(lo, y)) if forward else (Point(lo, y), Point(hi, y))
    try:
        await asyncio.to_thread(actions.swipe, instance_id, start, end)
    except Exception:
        logger.debug("goto_calendar: swipe failed", exc_info=True)


async def _capture(actions: Any, instance_id: str) -> Any:
    try:
        return await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    except Exception:
        return None


async def _screen(detector: Any, frame: Any) -> str:
    try:
        return str(await detector.detect_screen(frame, expected="event.calendar"))
    except Exception:
        return "unknown"


async def open_calendar_tab(actions: Any, instance_id: str, ocr: Any) -> bool:
    """Open the Events panel and select the Calendar tab (see module docs).

    Returns True only when ``event.calendar`` is verified on screen. If the tab
    can't be reached (e.g. an unattended click-approval swallowed a tap), the
    Events panel is backed out with a system-back and ``False`` is returned, so
    the bot ends on a known screen (main_city) rather than stranded on the
    primary-event page as ``currentNode == unknown`` — which would otherwise spin
    up the ``dismiss_unknown_popup`` recovery loop. ``ocr`` is accepted to mirror
    the sibling ``calendar_go`` walk and to build the detector.
    """
    from navigation.detector import ScreenDetector

    detector = ScreenDetector(ocr)

    # 1) open the events panel
    await _tap(actions, instance_id, _EVENTS_BUTTON, "events.button")
    await asyncio.sleep(1.5)

    # 2) swipe the carousel to its (still) leftmost page where Calendar lives
    prev_sig: Any = None
    for _ in range(_MAX_SWIPE):
        frame = await _capture(actions, instance_id)
        if frame is None:
            await asyncio.sleep(0.8)
            continue
        if await _screen(detector, frame) == "event.calendar":
            return True  # already on the Calendar tab
        sig = _strip_sig(frame)
        if prev_sig is not None and not _moved(prev_sig, sig):
            break  # strip went still → snapped at the leftmost page
        prev_sig = sig
        await _swipe(actions, instance_id, forward=False)
        await asyncio.sleep(0.8)

    # 3) tap the leftmost Calendar tab and verify we actually landed on it.
    await _tap(actions, instance_id, _CALENDAR_TAB, "calendar.tab")
    await asyncio.sleep(1.0)
    frame = await _capture(actions, instance_id)
    if frame is not None and await _screen(detector, frame) == "event.calendar":
        logger.info("goto_calendar: reached Calendar tab inst=%s", instance_id)
        return True

    # Calendar not reached — don't strand on the Events panel (unknown). Back out
    # so navigation fails cleanly to a known screen.
    logger.info(
        "goto_calendar: Calendar tab not verified inst=%s — backing out", instance_id
    )
    try:
        await asyncio.to_thread(actions.system_back, instance_id)
        await asyncio.sleep(0.8)
    except Exception:
        logger.debug("goto_calendar: system_back failed", exc_info=True)
    return False
