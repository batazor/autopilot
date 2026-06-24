"""Validate the three hook feature detectors against the reference frame.

``references/gameplay.png`` is the protected-hook state: anchor near x~352, blue
shield ring r~47, green node above it, fishing line down the same column."""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest
from games.wos.events.fishing_tournament import hook_detect as hd

_FRAME = Path(__file__).resolve().parents[1] / "references" / "gameplay.png"


@pytest.fixture(scope="module")
def frame():
    img = cv2.imread(str(_FRAME))
    assert img is not None, f"missing reference frame {_FRAME}"
    return img


@pytest.fixture(scope="module")
def det(frame):
    return hd.detect_hook(frame)


def test_green_node_found_at_top_of_hook(det):
    assert det.green_node is not None
    x, y = det.green_node
    assert x == pytest.approx(352, abs=15)
    assert y == pytest.approx(127, abs=15)


def test_blue_ring_is_a_near_perfect_circle(det):
    assert det.ring is not None, "protection ring should be detected on this frame"
    assert det.ring.x == pytest.approx(352, abs=15)
    assert det.ring.y == pytest.approx(194, abs=15)
    assert det.ring.r == pytest.approx(47, abs=12)
    # The defining property: an ideal circle (bbox aspect ratio ~1.0).
    assert det.ring.circularity > 0.9


def test_black_line_runs_down_the_hook_column(det):
    assert det.line is not None
    assert det.line.x == pytest.approx(350, abs=15)
    assert det.line.y_top < 10          # starts at the top of the screen
    assert det.line.y_bottom > 80        # reaches down to the hook


def test_three_features_agree_on_the_hook_column(det):
    """Ring centre, green node and line all pin the same x — the hook column."""
    xs = [det.ring.x, det.green_node[0], det.line.x]
    assert max(xs) - min(xs) < 40


def test_protected_flag_set_when_ring_present(det):
    assert det.protected is True
    assert det.center is not None


def test_no_false_ring_when_anchor_is_off_in_empty_water(frame):
    """Anchored far from the hook (empty water) → no ring, no false positive
    from the same-blue icebergs."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    ring = hd.detect_blue_ring(hsv, anchor_x=550)
    assert ring is None
