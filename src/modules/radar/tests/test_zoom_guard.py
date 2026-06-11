"""View guard: a capture must register against the previous frame or the
scan aborts — accidental zoom gestures must not produce a torn map."""

import cv2
import numpy as np
import pytest

from modules.radar.config import (
    CornersConfig,
    CropConfig,
    MinimapConfig,
    RadarConfig,
    StitchViewportConfig,
    TimingsConfig,
    ViewportConfig,
)
from modules.radar.scanner import ScanAborted, _guarded_capture
from modules.radar.tests.test_stitch_matching import _make_world

FRAME_W, FRAME_H = 480, 360


def _cfg() -> RadarConfig:
    return RadarConfig(
        minimap=MinimapConfig(
            bbox=(0, 0, 200, 200),
            corners=CornersConfig(
                top=(100, 0), right=(200, 100), bottom=(100, 200), left=(0, 100),
            ),
        ),
        viewport=ViewportConfig(rect_w=24, rect_h=39),
        crop=CropConfig(x=0, y=0, w=FRAME_W, h=FRAME_H),
        stitch_viewport=StitchViewportConfig(w=FRAME_W, h=FRAME_H),
        timings=TimingsConfig(
            stabilize_interval_ms=10,
            stabilize_timeout_ms=300,
            zoom_retry_delay_ms=10,
            zoom_retry_count=2,
        ),
    )


class FakeDevice:
    """Returns the queued frame on every capture (stable by construction).

    One ``wait_stable`` round consumes exactly 3 captures (initial + the two
    consecutive identical frames the stabilizer needs), so the queue advances
    one entry per round.
    """

    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = frames
        self.captures = 0

    def capture(self) -> np.ndarray:
        self.captures += 1
        index = min(len(self._frames) - 1, (self.captures - 1) // 3)
        return self._frames[index]


def test_consistent_pan_passes() -> None:
    world = _make_world(21, 700, 1000)
    prev = world[100 : 100 + FRAME_H, 100 : 100 + FRAME_W]
    cur = world[100 : 100 + FRAME_H, 300 : 300 + FRAME_W]  # pan +200 px
    device = FakeDevice([cur])

    frame, _stable = _guarded_capture(device, _cfg(), prev, expected=(200.0, 0.0))

    assert np.array_equal(frame, cur)


def test_first_frame_is_unguarded() -> None:
    flat = np.full((FRAME_H, FRAME_W, 3), 128, np.uint8)
    device = FakeDevice([flat])
    frame, _stable = _guarded_capture(device, _cfg(), None, expected=None)
    assert np.array_equal(frame, flat)


def test_zoomed_view_retries_then_aborts(tmp_path) -> None:
    world = _make_world(23, 700, 1000)
    prev = world[100 : 100 + FRAME_H, 100 : 100 + FRAME_W]
    # Accidental double-tap zoom: same area, 1.4x scale — never registers.
    zoom_src = world[150 : 150 + int(FRAME_H / 1.4), 170 : 170 + int(FRAME_W / 1.4)]
    zoomed = cv2.resize(zoom_src, (FRAME_W, FRAME_H))
    device = FakeDevice([zoomed])
    reject_path = tmp_path / "rejected_01_00.png"

    with pytest.raises(ScanAborted, match="zoom or view changed"):
        _guarded_capture(
            device, _cfg(), prev, expected=(200.0, 0.0), reject_path=reject_path,
        )

    # Initial capture + 2 retries → 3 wait_stable rounds happened.
    assert device.captures >= 3
    # Evidence on disk: the rejected frame shows what the camera actually saw.
    assert reject_path.is_file()


def test_recovers_when_view_settles_on_retry() -> None:
    world = _make_world(25, 700, 1000)
    prev = world[100 : 100 + FRAME_H, 100 : 100 + FRAME_W]
    good = world[100 : 100 + FRAME_H, 300 : 300 + FRAME_W]
    transition = np.full((FRAME_H, FRAME_W, 3), 30, np.uint8)  # dark fade
    # First wait_stable sees the transition frame, the retry sees the world.
    device = FakeDevice([transition, good])

    frame, _stable = _guarded_capture(device, _cfg(), prev, expected=(200.0, 0.0))

    assert np.array_equal(frame, good)
