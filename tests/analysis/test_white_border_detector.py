"""Pixel-level checks for ``layout.white_border_detector``.

Ground truth comes from 3 sequential frames captured ~10-30 s apart on the
VIP Point Rewards screen — ``tests/fixtures/white_border_dyn_{1,2,3}.png``.
The first reward tile (``Reach 5 Points (5/5)``) is claimable and is drawn
with an **animated** near-white outline that pulses across frames; the
remaining 3 tiles share the same purple icon body but sit on the bare
saturated row card with no outline.

The detector must:

* fire on the claimable tile across **all** 3 animation phases (else its
  output flickers during the animation, which the DSL guard can't tolerate);
* stay quiet on the 3 inactive tiles in every frame.

Synthetic frames lock in the discrimination rule itself: a halo painted
near-white → True; the same icon body without any outline → False.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from layout.white_border_detector import (
    find_white_border_match_in_search_roi,
    has_white_border_in_bbox_percent,
    white_border_halo_stats,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DYN_FIXTURES = (
    REPO_ROOT / "tests" / "fixtures" / "white_border_dyn_1.png",
    REPO_ROOT / "tests" / "fixtures" / "white_border_dyn_2.png",
    REPO_ROOT / "tests" / "fixtures" / "white_border_dyn_3.png",
    REPO_ROOT / "tests" / "fixtures" / "white_border_dyn_4.png",
)

# Row 1 (claimable, "Reach 5 Points (5/5)") icon location in 720×1280 frames.
# Slide-find should land within this rectangle on every animation phase.
_ROW1_TRUE_RECT_PX: tuple[int, int, int, int] = (158, 295, 93, 92)

# Pixel bboxes of the 4 reward-tile icons in the 720×1280 fixtures, ordered
# top → bottom. First is claimable (animated white outline), rest are not.
# Derived by HSV-purple segmentation on the captured frames; the bboxes are
# identical across the 3 frames because the UI layout is static — only the
# outline brightness animates.
_ICONS_PX: tuple[tuple[str, int, int, int, int], ...] = (
    ("row1_reach5_claimable", 158, 295, 93, 92),
    ("row2_reach25",          158, 501, 93, 93),
    ("row3_reach50",          158, 707, 93, 93),
    ("row4_reach80",          158, 913, 93, 92),
)


def _px_bbox_to_percent(
    x: int, y: int, w: int, h: int, image_w: int, image_h: int
) -> dict[str, float]:
    return {
        "x": x / image_w * 100.0,
        "y": y / image_h * 100.0,
        "width": w / image_w * 100.0,
        "height": h / image_h * 100.0,
    }


@pytest.mark.parametrize("fixture", DYN_FIXTURES, ids=lambda p: p.name)
def test_detects_animated_white_border_on_claimable_tile(fixture: Path) -> None:
    """The outline animates — the detector must fire across every captured
    phase, not just the brightest one."""
    img = cv2.imread(str(fixture))
    assert img is not None, f"missing fixture: {fixture}"
    h, w = img.shape[:2]
    name, x, y, iw, ih = _ICONS_PX[0]
    bbox = _px_bbox_to_percent(x, y, iw, ih, w, h)
    assert has_white_border_in_bbox_percent(img, bbox), (
        f"{name} @ {fixture.name}: expected white border, "
        f"halo stats={white_border_halo_stats(img, bbox)}"
    )


@pytest.mark.parametrize("fixture", DYN_FIXTURES, ids=lambda p: p.name)
def test_no_white_border_on_inactive_tiles(fixture: Path) -> None:
    img = cv2.imread(str(fixture))
    assert img is not None
    h, w = img.shape[:2]
    for name, x, y, iw, ih in _ICONS_PX[1:]:
        bbox = _px_bbox_to_percent(x, y, iw, ih, w, h)
        assert not has_white_border_in_bbox_percent(img, bbox), (
            f"{name} @ {fixture.name}: expected no white border, halo stats="
            f"{white_border_halo_stats(img, bbox)}"
        )


def test_animation_phase_halo_stays_inside_calibration_envelope() -> None:
    """Pin the animated-frame halo numbers so a future detector tweak doesn't
    silently erode the discrimination margin. Across the 3 sampled phases the
    claimable halo S sits in ~91-100 and the gap (interior_S − halo_S) ranges
    +16 to +25; inactive tiles sit at gap ≈ −75. The detector's S floor of
    10 must remain comfortably below the minimum gap observed here."""
    claimable_gaps: list[float] = []
    inactive_gaps: list[float] = []
    for fixture in DYN_FIXTURES:
        img = cv2.imread(str(fixture))
        assert img is not None
        h, w = img.shape[:2]
        for idx, (name, x, y, iw, ih) in enumerate(_ICONS_PX):
            bbox = _px_bbox_to_percent(x, y, iw, ih, w, h)
            halo_s, _halo_v, inner_s, ring = white_border_halo_stats(img, bbox)
            assert ring > 500, f"{name} @ {fixture.name}: halo too small ({ring} px)"
            gap = inner_s - halo_s
            if idx == 0:
                claimable_gaps.append(gap)
            else:
                inactive_gaps.append(gap)

    assert min(claimable_gaps) > 12.0, (
        f"animated claimable halo dipped to gap={min(claimable_gaps)} — detector "
        f"threshold (10) no longer has safe margin against this animation phase"
    )
    assert max(inactive_gaps) < 0.0, (
        f"inactive halo gap rose to {max(inactive_gaps)} — discrimination eroded"
    )


def test_synthetic_white_halo_fires() -> None:
    """A purple square painted on a near-white card — the halo is bright and
    desaturated and the interior is more saturated, so the detector fires."""
    img = np.full((200, 200, 3), 245, dtype=np.uint8)
    cv2.rectangle(img, (70, 70), (130, 130), (160, 50, 130), thickness=-1)
    bbox = {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0}
    assert has_white_border_in_bbox_percent(img, bbox)


def test_synthetic_saturated_halo_quiet() -> None:
    """Same icon on a saturated cyan card (the row background in-game). Halo
    is bright but saturated — must not register as a white border."""
    img = np.full((200, 200, 3), (220, 200, 50), dtype=np.uint8)
    cv2.rectangle(img, (70, 70), (130, 130), (160, 50, 130), thickness=-1)
    bbox = {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0}
    assert not has_white_border_in_bbox_percent(img, bbox)


def test_synthetic_white_on_white_quiet() -> None:
    """A near-white bbox on a near-white card has a bright + desaturated halo
    but no contrasting interior — the contrast gate must reject it."""
    img = np.full((200, 200, 3), 245, dtype=np.uint8)
    cv2.rectangle(img, (70, 70), (130, 130), (240, 240, 240), thickness=-1)
    bbox = {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0}
    assert not has_white_border_in_bbox_percent(img, bbox)


def test_bbox_at_image_edge_abstains() -> None:
    """Halo gets clipped to a sliver when bbox hugs the image edge. With too
    few ring pixels the detector returns False rather than guessing."""
    img = np.full((200, 200, 3), 245, dtype=np.uint8)
    bbox = {"x": 0.0, "y": 0.0, "width": 1.0, "height": 1.0}
    assert not has_white_border_in_bbox_percent(img, bbox)


def test_malformed_input_returns_false() -> None:
    assert not has_white_border_in_bbox_percent(
        None, {"x": 10, "y": 10, "width": 10, "height": 10}  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    )
    assert not has_white_border_in_bbox_percent(np.zeros((10, 10), dtype=np.uint8), {})
    assert not has_white_border_in_bbox_percent(
        np.zeros((10, 10, 3), dtype=np.uint8), {"x": 1.0}
    )


# ---------------------------------------------------------------------------
# Contour-based ``find_white_border_match_in_search_roi``
# ---------------------------------------------------------------------------


def _rect_overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw < bx or bx + bw < ax or ay + ah < by or by + bh < ay)


def _center_inside(cx: int, cy: int, rect: tuple[int, int, int, int]) -> bool:
    x, y, w, h = rect
    return x <= cx <= x + w and y <= cy <= y + h


@pytest.mark.parametrize("fixture", DYN_FIXTURES, ids=lambda p: p.name)
def test_slide_find_locates_row1_on_full_screen(fixture: Path) -> None:
    """Search the whole frame: the contour-based finder must land on row 1
    across every animation phase, including the dimmest sampled frame
    (dyn_3) where only part of the outline crosses the V threshold."""
    img = cv2.imread(str(fixture))
    assert img is not None, f"missing fixture: {fixture}"
    h, w = img.shape[:2]
    result = find_white_border_match_in_search_roi(img, None)
    assert result is not None, f"slide-find returned None on {fixture.name}"
    cx_pct = float(result["cx_pct"])  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    cy_pct = float(result["cy_pct"])  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    cx_px = int(round(cx_pct / 100.0 * w))
    cy_px = int(round(cy_pct / 100.0 * h))
    assert _center_inside(cx_px, cy_px, _ROW1_TRUE_RECT_PX), (
        f"{fixture.name}: click center ({cx_px},{cy_px}) outside row 1 rect "
        f"{_ROW1_TRUE_RECT_PX}; result={result}"
    )


def test_slide_find_returns_none_when_no_highlight() -> None:
    """A synthetic frame with no near-white outline at all → finder abstains."""
    img = np.full((400, 400, 3), (200, 100, 100), dtype=np.uint8)  # solid blue
    cv2.rectangle(img, (100, 100), (300, 300), (50, 50, 50), thickness=-1)
    assert find_white_border_match_in_search_roi(img, None) is None


def test_slide_find_returns_none_when_interior_is_grey() -> None:
    """A white frame around an empty grey area must not fire — the interior
    saturation gate exists specifically to reject text labels and lock badges
    whose interior averages near-grey rather than a saturated colour."""
    img = np.full((400, 400, 3), (180, 180, 180), dtype=np.uint8)  # mid grey
    # Bright white rectangle outline around a grey interior
    cv2.rectangle(img, (100, 100), (300, 200), (255, 255, 255), thickness=4)
    assert find_white_border_match_in_search_roi(img, None) is None


def test_slide_find_respects_search_roi() -> None:
    """Limiting the search ROI must not pick up matches outside it — even if
    a strong highlight exists elsewhere in the frame, the finder must abstain
    when the ROI is bounded away from it."""
    img = cv2.imread(str(DYN_FIXTURES[0]))
    assert img is not None
    _h, _w = img.shape[:2]
    # ROI = bottom-right quadrant — row 1 highlight is upper-left, not here.
    roi = {"x": 50.0, "y": 50.0, "width": 50.0, "height": 50.0}
    assert find_white_border_match_in_search_roi(img, roi) is None


def test_slide_find_malformed_input_returns_none() -> None:
    assert find_white_border_match_in_search_roi(None) is None  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    assert find_white_border_match_in_search_roi(
        np.zeros((10, 10), dtype=np.uint8)
    ) is None
    assert find_white_border_match_in_search_roi(
        np.zeros((10, 10, 3), dtype=np.uint8),
        {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0},
    ) is None
