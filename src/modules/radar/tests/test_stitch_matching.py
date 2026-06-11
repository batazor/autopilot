"""Edge matching used by radar stitch."""

import cv2
import numpy as np
import pytest

from modules.radar.stitch import _estimate_pair_offset, _match_image, _valid_content_mask


def test_estimate_pair_offset_recovers_small_swipe_drift() -> None:
    rng = np.random.default_rng(7)
    canvas = rng.integers(0, 256, (700, 900, 3), dtype=np.uint8)
    canvas = cv2.GaussianBlur(canvas, (5, 5), 0)
    frame_w, frame_h = 300, 240
    expected_dx, expected_dy = 120, 60
    drift_x, drift_y = 17, -11
    actual_dx = expected_dx + drift_x
    actual_dy = expected_dy + drift_y

    a = canvas[100 : 100 + frame_h, 100 : 100 + frame_w]
    b = canvas[
        100 + actual_dy : 100 + actual_dy + frame_h,
        100 + actual_dx : 100 + actual_dx + frame_w,
    ]

    estimate = _estimate_pair_offset(
        _match_image(a),
        _match_image(b),
        expected_dx,
        expected_dy,
    )

    assert estimate is not None
    dx, dy, score = estimate
    assert dx == pytest.approx(actual_dx, abs=0.5)
    assert dy == pytest.approx(actual_dy, abs=0.5)
    assert score > 0.3


def test_valid_content_mask_uses_yellow_boundary_to_drop_dark_outside() -> None:
    img = np.full((180, 180, 3), (210, 220, 240), dtype=np.uint8)
    dark_poly = np.array([[(0, 0), (180, 0), (0, 180)]], dtype=np.int32)
    cv2.fillPoly(img, dark_poly, (34, 36, 44))
    for start in range(-20, 180, 18):
        cv2.line(
            img,
            (max(start, 0), max(0, 170 - start)),
            (min(start + 10, 179), max(0, 170 - start - 10)),
            (120, 230, 235),
            4,
        )

    mask = _valid_content_mask(img)

    assert mask[8, 8] == 0
    assert mask[150, 150] == 255
    # Yellow boundary itself is kept so the stitched map still shows the edge.
    assert np.count_nonzero(mask[(img[:, :, 1] > 220) & (img[:, :, 2] > 220)]) > 0
