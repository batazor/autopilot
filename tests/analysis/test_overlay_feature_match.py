"""ORB-based ``feature_match`` recognition mode in :mod:`analysis.overlay_engine`."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from analysis.overlay_engine import _match_orb_features_in_bbox


def _textured_patch(width: int, height: int, seed: int) -> np.ndarray:
    """Composite of shapes + text — FAST corners that ORB can latch onto.

    Pure random per-pixel noise gives no detectable keypoints (no
    multi-pixel structural contrast). Real UI icons have edges and corners
    which is what ORB scores, so the synthetic template mirrors that shape.
    """
    rng = np.random.default_rng(seed)
    img = np.full((height, width, 3), 32, dtype=np.uint8)
    for _ in range(6):
        x = int(rng.integers(0, max(1, width - 12)))
        y = int(rng.integers(0, max(1, height - 12)))
        w = int(rng.integers(6, max(7, width // 3)))
        h = int(rng.integers(6, max(7, height // 3)))
        color = tuple(int(c) for c in rng.integers(80, 255, size=3))
        cv2.rectangle(img, (x, y), (x + w, y + h), color, thickness=-1)
    for _ in range(4):
        cx = int(rng.integers(4, max(5, width - 4)))
        cy = int(rng.integers(4, max(5, height - 4)))
        r = int(rng.integers(3, max(4, min(width, height) // 6)))
        color = tuple(int(c) for c in rng.integers(60, 255, size=3))
        cv2.circle(img, (cx, cy), r, color, thickness=2)
    cv2.putText(img, f"S{seed}", (4, max(12, height // 2)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return img


def _place_patch(frame: np.ndarray, patch: np.ndarray, x: int, y: int) -> None:
    h, w = patch.shape[:2]
    frame[y : y + h, x : x + w] = patch


@pytest.fixture
def synthetic_frame_and_template() -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    frame = _textured_patch(640, 480, seed=1)
    template = _textured_patch(80, 60, seed=2)
    place_x, place_y = 220, 140
    _place_patch(frame, template, place_x, place_y)
    return frame, template, (place_x, place_y)


def test_orb_locates_template_inside_full_frame(
    synthetic_frame_and_template: tuple[np.ndarray, np.ndarray, tuple[int, int]],
) -> None:
    frame, template, (px, py) = synthetic_frame_and_template
    full_bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

    res = _match_orb_features_in_bbox(frame, template, full_bbox)

    assert res["match_source"] == "orb_features"
    assert res["template_w"] == template.shape[1]
    assert res["template_h"] == template.shape[0]
    assert res["score"] > 0.5, res
    tlx, tly = res["top_left"]
    assert abs(tlx - px) <= 3, (tlx, px)
    assert abs(tly - py) <= 3, (tly, py)


def test_orb_returns_zero_when_template_absent() -> None:
    frame = _textured_patch(640, 480, seed=3)
    template = _textured_patch(80, 60, seed=4)
    full_bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

    res = _match_orb_features_in_bbox(frame, template, full_bbox)

    assert res["score"] == 0.0
    assert res["match_source"] == "orb_features"


def test_orb_handles_flat_blank_search_region() -> None:
    frame = np.full((480, 640, 3), 64, dtype=np.uint8)
    template = _textured_patch(60, 60, seed=5)
    full_bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

    res = _match_orb_features_in_bbox(frame, template, full_bbox)

    assert res["score"] == 0.0


def test_orb_finds_template_after_mild_blur(
    synthetic_frame_and_template: tuple[np.ndarray, np.ndarray, tuple[int, int]],
) -> None:
    """Template-match (NCC) tolerates blur poorly; ORB should still recover the location.

    This is the load-bearing reason for adding feature_match: robustness to mild
    visual drift (anti-aliasing, slight UI restyle) that breaks pixel-exact NCC.
    """
    frame, template, (px, py) = synthetic_frame_and_template
    blurred = cv2.GaussianBlur(frame, (3, 3), 0)
    full_bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

    res = _match_orb_features_in_bbox(blurred, template, full_bbox)

    assert res["score"] > 0.4, res
    tlx, tly = res["top_left"]
    assert abs(tlx - px) <= 5
    assert abs(tly - py) <= 5
