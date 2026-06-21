"""find_kingdom_corner: gate a border crossing on the out-of-bounds gap.

The interior of a kingdom is patterned with alliance/territory diamonds whose
yellow edges also cross — ``find_border_cross`` fires on them. Only the true
kingdom corner backs onto the dark inter-state gap, so the gated detector must
accept a crossing with a gap behind it and reject an identical crossing without
one. (Validated against a real scan: true corner ~0.85 outside-fraction, every
interior false crossing 0.00.)
"""

from __future__ import annotations

import cv2
import numpy as np

from modules.radar import border


def _interior() -> np.ndarray:
    return np.full((300, 300, 3), 210, np.uint8)  # bright snow, in-bounds


def _yellow_v(frame: np.ndarray) -> None:
    # Two thin yellow lines (one +slope, one -slope) crossing at (150, 200) —
    # the shape of both a real corner and an interior alliance vertex.
    cv2.line(frame, (60, 80), (150, 200), (0, 255, 255), 3)
    cv2.line(frame, (240, 80), (150, 200), (0, 255, 255), 3)


def test_cross_fires_on_a_bare_v() -> None:
    frame = _interior()
    _yellow_v(frame)
    assert border.find_border_cross(frame, None) is not None


def test_kingdom_corner_accepts_crossing_with_gap_behind() -> None:
    frame = _interior()
    _yellow_v(frame)
    frame[205:, :] = (60, 60, 60)  # dark, neutral out-of-bounds gap
    assert border.border_outside_fraction(frame, None) > 0.4
    assert border.find_kingdom_corner(frame, None) is not None


def test_kingdom_corner_rejects_interior_crossing_without_gap() -> None:
    frame = _interior()
    _yellow_v(frame)  # crossing present, but nothing out-of-bounds behind it
    assert border.find_border_cross(frame, None) is not None
    assert border.find_kingdom_corner(frame, None) is None


def test_kingdom_corner_accepts_a_tinted_gap() -> None:
    # The live-screen gap is dark but strongly colour-tinted (snow-blue), which
    # the neutral-grey border_outside_fraction discards (it read 0 at a real
    # corner). find_kingdom_corner must hold via the tint-tolerant darkness.
    frame = _interior()
    _yellow_v(frame)
    frame[205:, :] = (95, 55, 35)  # gray ~54 (dark), spread 60 (tinted, not grey)
    assert border.border_outside_fraction(frame, None) < 0.2  # neutral signal misses it
    assert border.border_darkness_fraction(frame, None) > 0.4  # tint-tolerant catches it
    assert border.find_kingdom_corner(frame, None) is not None
