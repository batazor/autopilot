"""Border landmarks: dashed-line crossing (the corner) + top-corner arrival."""

import cv2
import numpy as np
import pytest

from modules.radar.border import (
    border_band_y,
    border_cross_distance,
    border_outside_fraction,
    border_outside_top_y,
    find_border_cross,
    top_border_visible,
    yellow_boundary_mask,
)

YELLOW_BGR = (120, 230, 235)
SNOW_BGR = (230, 222, 218)


def _frame(h: int = 400, w: int = 400) -> np.ndarray:
    return np.full((h, w, 3), SNOW_BGR, dtype=np.uint8)


def _dash_line(img: np.ndarray, p1: tuple[int, int], p2: tuple[int, int]) -> None:
    """Dashed yellow line — thin segments like the in-game border.

    The final dash always ends exactly at ``p2`` so a V built from two lines
    truly converges at the requested apex.
    """
    n = 14
    for k in range(0, n, 2):
        a = (
            int(p1[0] + (p2[0] - p1[0]) * k / n),
            int(p1[1] + (p2[1] - p1[1]) * k / n),
        )
        b = (
            int(p1[0] + (p2[0] - p1[0]) * (k + 1) / n),
            int(p1[1] + (p2[1] - p1[1]) * (k + 1) / n),
        )
        cv2.line(img, a, b, YELLOW_BGR, 4)
    tail = (
        int(p1[0] + (p2[0] - p1[0]) * (n - 1) / n),
        int(p1[1] + (p2[1] - p1[1]) * (n - 1) / n),
    )
    cv2.line(img, tail, p2, YELLOW_BGR, 4)


def _x_frame(cross: tuple[int, int]) -> np.ndarray:
    """Two dashed lines crossing at ``cross`` with tails past it — like the
    in-game corner, where the border lines extend beyond their intersection."""
    img = _frame()
    _dash_line(img, (cross[0] - 180, cross[1] - 180), (cross[0] + 70, cross[1] + 70))
    _dash_line(img, (cross[0] + 180, cross[1] - 180), (cross[0] - 70, cross[1] + 70))
    return img


def test_mask_keeps_the_dashed_line_and_drops_gold_blobs() -> None:
    img = _x_frame((200, 300))
    cv2.circle(img, (80, 80), 30, (60, 200, 230), -1)  # solid gold castle blob

    mask = yellow_boundary_mask(img)

    assert mask[290:302, 194:206].any()  # line near the crossing survives
    assert not mask[80, 80]  # blob removed


def test_find_border_cross_locates_the_x() -> None:
    cross = find_border_cross(_x_frame((220, 260)), None)
    assert cross is not None
    assert cross[0] == pytest.approx(220, abs=10)
    assert cross[1] == pytest.approx(260, abs=10)


def test_find_border_cross_rejects_a_single_side_line() -> None:
    # One border line sweeping the frame diagonally — the failure mode that
    # used to fake an origin lock. One slope sign only → no corner.
    img = _frame()
    _dash_line(img, (10, 60), (390, 360))
    assert find_border_cross(img, None) is None


def test_find_border_cross_respects_the_crop() -> None:
    # The crossing sits far below the crop — outside the inspected area.
    img = _x_frame((200, 340))
    crop = {"x": 0, "y": 0, "w": 400, "h": 120}
    assert find_border_cross(img, crop) is None


def test_find_border_cross_none_on_plain_terrain() -> None:
    assert find_border_cross(_frame(), None) is None


def test_border_outside_fraction_separates_inside_from_the_gap() -> None:
    # All bright snow → nothing out-of-bounds.
    snow = _frame()
    assert border_outside_fraction(snow, None) < 0.05

    # Lower half dark and connected to the bottom edge → the inter-kingdom gap.
    gap = _frame()
    gap[200:, :] = (40, 42, 50)
    assert border_outside_fraction(gap, None) > 0.9

    # A dark blob NOT touching any frame edge (interior terrain) is not counted.
    terrain = _frame()
    terrain[260:340, 160:240] = (40, 42, 50)
    assert border_outside_fraction(terrain, None) < 0.1


def test_border_outside_top_y_finds_the_dark_edge() -> None:
    img = _frame()
    img[300:, :] = (40, 42, 50)
    top = border_outside_top_y(img, None)
    assert top is not None
    assert top == pytest.approx(300, abs=12)


def test_border_outside_top_y_ignores_upper_half_and_clean_terrain() -> None:
    assert border_outside_top_y(_frame(), None) is None
    # Dark connected to the TOP edge only — not the bottom-corner gap; ignoring
    # the upper half keeps unrelated dark areas from hijacking the steering.
    img = _frame()
    img[:80, :] = (40, 42, 50)
    assert border_outside_top_y(img, None) is None


def test_border_band_y_tracks_the_visible_line() -> None:
    # Proper border-slope (~0.5) dashed line from (60, 60) to (360, 210).
    img = _frame()
    _dash_line(img, (60, 60), (360, 210))
    band = border_band_y(img, None)
    assert band is not None
    assert band == pytest.approx(135, abs=20)

    assert border_band_y(_frame(), None) is None


def test_border_band_y_rejects_scattered_icon_noise() -> None:
    """Golden icons leave plenty of yellow-ish pixels with no line structure —
    a bare median over them once steered the servo for 12 steps on garbage.
    Without a Hough-fittable segment there must be NO band reading."""
    img = _frame()
    rng = np.random.default_rng(7)
    for _ in range(60):  # ~icon-edge specks, way past BORDER_MIN_PIXELS total
        x, y = int(rng.integers(10, 390)), int(rng.integers(10, 390))
        cv2.line(img, (x, y), (x + 3, y), YELLOW_BGR, 2)
    assert border_band_y(img, None) is None


def test_border_cross_distance_measures_the_line_ahead() -> None:
    # Vertical dashed border 120 px right of the frame center (200, 200).
    img = _frame()
    _dash_line(img, (320, 0), (320, 399))

    dist = border_cross_distance(img, None, 100.0, 0.0)
    assert dist == pytest.approx(120, abs=8)

    # Moving away from the line — the path behind does not count.
    assert border_cross_distance(img, None, -100.0, 0.0) is None


def test_border_cross_distance_ignores_lines_outside_the_corridor() -> None:
    img = _frame()
    _dash_line(img, (320, 0), (320, 399))
    # Moving straight down: the line sits 120 px off the motion axis.
    assert border_cross_distance(img, None, 0.0, 100.0, corridor_px=60.0) is None


def test_border_cross_distance_none_on_plain_terrain() -> None:
    assert border_cross_distance(_frame(), None, 100.0, 0.0) is None


def test_top_border_visible_requires_a_high_crossing() -> None:
    # Inverted V at the top: lines cross high, bulk descends below.
    top = _frame()
    _dash_line(top, (40, 70), (200, 12))
    _dash_line(top, (360, 70), (200, 12))
    assert top_border_visible(top, None) is True

    # A single side border crossing the frame: one slope sign only.
    side = _frame()
    _dash_line(side, (30, 0), (110, 399))
    assert top_border_visible(side, None) is False

    assert top_border_visible(_frame(), None) is False


def test_top_border_not_fooled_by_the_bottom_corner() -> None:
    """The bottom corner's V arms also reach the top of the frame on both
    halves — that used to fire the band test and end a scan 3 frames in.
    Its crossing sits low and the line bulk is above it: must NOT trigger."""
    assert top_border_visible(_x_frame((200, 260)), None) is False


def test_outside_dark_distance_is_directional() -> None:
    from modules.radar.border import outside_dark_distance, outside_visible

    img = _frame()
    img[300:, :] = (40, 42, 50)  # dark gap along the bottom edge

    assert outside_visible(img, None)
    # Toward the gap: the mass starts ~100 px below the center (200).
    down = outside_dark_distance(img, None, 0.0, 500.0)
    assert down == pytest.approx(100, abs=15)
    # Away from it: nothing dark ahead.
    assert outside_dark_distance(img, None, 0.0, -500.0) is None
    # Plain frame: no mass at all.
    assert not outside_visible(_frame(), None)
    assert outside_dark_distance(_frame(), None, 0.0, 500.0) is None


def test_outside_mask_excludes_tinted_dark_but_fills_gap_holes() -> None:
    from modules.radar.border import border_outside_fraction, outside_visible

    img = _frame()
    # Neutral-dark gap over the lower half, with a tinted monster ON the road.
    img[200:, :] = (40, 42, 50)
    cv2.rectangle(img, (150, 280), (230, 340), (30, 60, 120), -1)  # brown sprite
    frac = border_outside_fraction(img, None)
    # The enclosed sprite is absorbed into the gap, not a hole in it.
    assert frac > 0.95

    # Tinted dark content alone (a mountain at the edge) is NOT the gap.
    mountain = _frame()
    cv2.rectangle(mountain, (0, 150), (120, 320), (130, 60, 35), -1)
    assert not outside_visible(mountain, None)
