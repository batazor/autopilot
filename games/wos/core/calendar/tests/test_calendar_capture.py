"""Tests for the calendar scroll-to-bottom capture.

Pure helpers (``content_delta`` / ``reached_bottom``) are exercised on synthetic
frames; the async ``scroll_capture_calendar`` runs against a fake actions object
that replays a scripted scroll (frames change, then freeze at the bottom).
"""
from __future__ import annotations

import numpy as np
from games.wos.core.calendar import capture


def _frame(value: int) -> np.ndarray:
    return np.full((1280, 720, 3), value, dtype=np.uint8)


def test_content_delta_zero_for_identical_frames():
    f = _frame(100)
    assert capture.content_delta(f, f.copy()) == 0.0


def test_content_delta_large_when_region_changes():
    a = _frame(0)
    b = _frame(0)
    # change the scrollable region only
    h, w = b.shape[:2]
    x, y, rw, rh = capture._region(capture.CONTENT_BBOX_PCT, w, h)
    b[y : y + rh, x : x + rw] = 200
    assert capture.content_delta(a, b) > capture.DEFAULT_DIFF_THRESHOLD


def test_content_delta_inf_on_shape_mismatch():
    assert capture.content_delta(_frame(0), None) == float("inf")
    assert capture.content_delta(np.zeros((10, 10, 3), np.uint8), _frame(0)) == float("inf")


def test_reached_bottom_threshold():
    assert capture.reached_bottom(0.04) is True       # measured bottom delta
    assert capture.reached_bottom(20.0) is False       # measured scrolling delta


class _FakeActions:
    """Replays a scripted scroll: each capture returns the next frame until the
    list runs out, then repeats the last one (= bottom reached)."""

    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = frames
        self._i = 0
        self.swipes = 0

    def capture_screen_bgr(self, instance_id):
        return self._frames[min(self._i, len(self._frames) - 1)]

    def swipe(self, instance_id, start, end, duration_ms):
        self._i += 1
        self.swipes += 1
        return True


class _RisingActions:
    """Content always changes — used to confirm the swipe cap is honoured."""

    def __init__(self) -> None:
        self.v = 0
        self.swipes = 0

    def capture_screen_bgr(self, instance_id):
        return _frame(self.v % 250)

    def swipe(self, instance_id, start, end, duration_ms):
        self.v += 60
        self.swipes += 1
        return True


async def test_scroll_capture_stops_at_bottom():
    # two distinct views, then the third swipe shows no change → bottom.
    frames = [_frame(10), _frame(120), _frame(120)]
    actions = _FakeActions(frames)
    out = await capture.scroll_capture_calendar(actions, "inst", settle_ms=0, max_swipes=6)
    assert len(out) == 2                 # the duplicate bottom frame is dropped
    assert actions.swipes == 2           # stopped as soon as content froze


async def test_scroll_capture_respects_max_swipes():
    # content always changes → capped by max_swipes.
    actions = _RisingActions()
    out = await capture.scroll_capture_calendar(actions, "inst", settle_ms=0, max_swipes=3)
    assert actions.swipes == 3
    assert len(out) == 4                 # initial + 3 changed views
