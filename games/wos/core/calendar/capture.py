"""Scroll the event calendar to the bottom, capturing every row.

The calendar schedule scrolls vertically under a sticky header (the day strip +
UTC clock) and footer, so a single screenshot only shows the top events. This
walks the list to the bottom: swipe the scrollable area up, wait for it to
settle, capture, and stop when the content stops moving (bottom reached) or a
swipe cap is hit. The returned frames feed the schedule parser (OCR, pending).

Geometry + thresholds were calibrated on a live 720×1280 device: the swipe
``(50%,74%)→(50%,35%)`` advances the list, and the content region's mean
absolute BGR delta runs ~18-26 while scrolling and ~0.02 once at the bottom —
so a threshold of 2.0 cleanly separates "moved" from "stuck".

Pure helpers (``content_delta`` / ``reached_bottom``) are unit tested; the async
``scroll_capture_calendar`` is the thin ADB-driving loop.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from layout.types import Point

logger = logging.getLogger(__name__)

# Scrollable rows region (percent of frame) — excludes the sticky tab strip /
# day-strip / clock at the top and the footer hint at the bottom, so the delta
# only reflects event rows moving.
CONTENT_BBOX_PCT = {"x": 0.0, "y": 17.0, "width": 100.0, "height": 75.0}
# Vertical swipe within the content to scroll DOWN (finger travels up).
SWIPE_FROM_PCT = (50.0, 74.0)
SWIPE_TO_PCT = (50.0, 35.0)

DEFAULT_MAX_SWIPES = 6          # bottom hit in ~2-3 on a full calendar; cap for safety
DEFAULT_SETTLE_MS = 1000        # let the fling settle before capturing
DEFAULT_DURATION_MS = 400
DEFAULT_DIFF_THRESHOLD = 2.0    # mean abs BGR delta: scrolling ≫ this, bottom < this


def _region(bbox_pct: dict[str, float], w: int, h: int) -> tuple[int, int, int, int]:
    x = int(round(bbox_pct["x"] / 100.0 * w))
    y = int(round(bbox_pct["y"] / 100.0 * h))
    rw = int(round(bbox_pct["width"] / 100.0 * w))
    rh = int(round(bbox_pct["height"] / 100.0 * h))
    return x, y, rw, rh


def content_delta(
    prev: np.ndarray | None,
    cur: np.ndarray | None,
    bbox_pct: dict[str, float] | None = None,
) -> float:
    """Mean absolute BGR difference of the scrollable region between two frames.

    ``inf`` when frames are missing or mismatched, so a bad capture never reads
    as "no movement" (which would prematurely stop the scroll).
    """
    if prev is None or cur is None or prev.shape != cur.shape:
        return float("inf")
    h, w = prev.shape[:2]
    x, y, rw, rh = _region(bbox_pct or CONTENT_BBOX_PCT, w, h)
    a = prev[y : y + rh, x : x + rw]
    b = cur[y : y + rh, x : x + rw]
    if a.size == 0 or a.shape != b.shape:
        return float("inf")
    return float(np.mean(np.abs(a.astype(np.int16) - b.astype(np.int16))))


def reached_bottom(delta: float, threshold: float = DEFAULT_DIFF_THRESHOLD) -> bool:
    """The list stopped moving — the last swipe revealed nothing new."""
    return delta < threshold


def _point(pct: tuple[float, float], w: int, h: int) -> Point:
    from layout.types import Point

    return Point(int(round(pct[0] / 100.0 * w)), int(round(pct[1] / 100.0 * h)))


async def scroll_to_top(
    actions: Any,
    instance_id: str,
    *,
    max_swipes: int = DEFAULT_MAX_SWIPES,
    settle_ms: int = DEFAULT_SETTLE_MS,
    duration_ms: int = DEFAULT_DURATION_MS,
    diff_threshold: float = DEFAULT_DIFF_THRESHOLD,
) -> None:
    """Swipe the calendar up to the top so a scan/search starts from event #1.

    Swipes *down* (reverse of the read direction) until the content stops moving
    — the list is at the top. Idempotent: a no-op if already at the top.
    """
    frame = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    if frame is None:
        return
    h, w = frame.shape[:2]
    # reverse of the read swipe: drag content downward to reveal earlier rows
    start, end = _point(SWIPE_TO_PCT, w, h), _point(SWIPE_FROM_PCT, w, h)
    for _ in range(max_swipes):
        await asyncio.to_thread(actions.swipe, instance_id, start, end, duration_ms)
        await asyncio.sleep(settle_ms / 1000.0)
        nxt = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        if nxt is None or reached_bottom(content_delta(frame, nxt), diff_threshold):
            break  # content stopped moving → top reached
        frame = nxt


async def scroll_capture_calendar(
    actions: Any,
    instance_id: str,
    *,
    max_swipes: int = DEFAULT_MAX_SWIPES,
    settle_ms: int = DEFAULT_SETTLE_MS,
    duration_ms: int = DEFAULT_DURATION_MS,
    diff_threshold: float = DEFAULT_DIFF_THRESHOLD,
) -> list[np.ndarray]:
    """Capture the calendar top-to-bottom. Returns one frame per distinct view.

    Assumes the caller is already on ``event.calendar`` (the scenario's ``node``
    guarantees it). Stops as soon as a swipe leaves the content unchanged, so a
    short calendar costs one extra swipe, not ``max_swipes``.
    """
    frame = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
    if frame is None:
        return []
    frames: list[np.ndarray] = [frame]
    h, w = frame.shape[:2]
    start, end = _point(SWIPE_FROM_PCT, w, h), _point(SWIPE_TO_PCT, w, h)

    for _ in range(max_swipes):
        await asyncio.to_thread(actions.swipe, instance_id, start, end, duration_ms)
        await asyncio.sleep(settle_ms / 1000.0)
        nxt = await asyncio.to_thread(actions.capture_screen_bgr, instance_id)
        if nxt is None:
            break
        if reached_bottom(content_delta(frames[-1], nxt), diff_threshold):
            break  # bottom: nxt duplicates the last view — don't keep it
        frames.append(nxt)

    logger.info(
        "calendar capture: instance=%s frames=%d (swipes=%d)",
        instance_id, len(frames), len(frames) - 1,
    )
    return frames
