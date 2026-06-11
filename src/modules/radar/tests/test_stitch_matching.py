"""Edge matching used by radar stitch."""

import json

import cv2
import numpy as np
import pytest

from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import (
    _estimate_pair_offset,
    _match_image,
    _valid_content_mask,
    run_stitch,
)


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


def test_run_stitch_places_uncropped_frames(tmp_path) -> None:
    """Frames are placed as-is: tile size comes from the image, not config."""
    rng = np.random.default_rng(3)
    world = cv2.GaussianBlur(
        rng.integers(0, 256, (600, 800, 3), dtype=np.uint8), (5, 5), 0
    )
    frame_w, frame_h = 400, 300
    overlap = 0.5
    step_x = int(frame_w * (1 - overlap))  # 200
    step_y = int(frame_h * (1 - overlap))  # 150

    frames = {}
    for ix, iy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        name = f"frame_{ix:02d}_{iy:02d}.png"
        x, y = ix * step_x, iy * step_y
        cv2.imwrite(str(tmp_path / name), world[y : y + frame_h, x : x + frame_w])
        frames[f"{ix:02d}_{iy:02d}"] = {"ix": ix, "iy": iy, "file": name}

    manifest = {
        "config": {
            "overlap": overlap,
            "stitch_viewport": {"w": frame_w, "h": frame_h},
            # crop intentionally differs from the frame size — must be ignored
            "crop": {"x": 0, "y": 156, "w": 620, "h": 940},
        },
        "frames": frames,
    }
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    out = run_stitch(tmp_path)

    canvas = cv2.imread(str(out))
    assert canvas is not None
    assert (canvas.shape[1], canvas.shape[0]) == (step_x + frame_w, step_y + frame_h)
    # Every pixel of the 2×2 mosaic should come straight from the world image.
    assert np.array_equal(canvas, world[: step_y + frame_h, : step_x + frame_w])


def test_run_stitch_measures_isometric_grid_basis(tmp_path) -> None:
    """A minimap grid step moves the screen diagonally — the stitcher must
    measure the real right/down screen vectors instead of assuming axis-aligned
    steps."""
    rng = np.random.default_rng(11)
    world = cv2.GaussianBlur(
        rng.integers(0, 256, (900, 1100, 3), dtype=np.uint8), (5, 5), 0
    )
    frame_w, frame_h = 480, 360
    right = (100, 50)   # screen shift per ix+1 (diagonal: isometry)
    down = (-50, 90)    # screen shift per iy+1

    base_x, base_y = 200, 150
    offsets = {}
    frames = {}
    for ix, iy in [(0, 0), (1, 0), (0, 1), (1, 1)]:
        px = ix * right[0] + iy * down[0]
        py = ix * right[1] + iy * down[1]
        offsets[(ix, iy)] = (px, py)
        name = f"frame_{ix:02d}_{iy:02d}.png"
        window = world[
            base_y + py : base_y + py + frame_h,
            base_x + px : base_x + px + frame_w,
        ]
        cv2.imwrite(str(tmp_path / name), window)
        frames[f"{ix:02d}_{iy:02d}"] = {"ix": ix, "iy": iy, "file": name}

    manifest = {
        "config": {
            "overlap": 0.5,
            # Deliberately wrong axis-aligned geometry: the measured basis
            # must override it.
            "stitch_viewport": {"w": frame_w, "h": frame_h},
        },
        "frames": frames,
    }
    (tmp_path / MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")

    out = run_stitch(tmp_path)

    canvas = cv2.imread(str(out))
    assert canvas is not None
    min_x = min(px for px, _ in offsets.values())
    min_y = min(py for _, py in offsets.values())
    expected_w = max(px for px, _ in offsets.values()) - min_x + frame_w
    expected_h = max(py for _, py in offsets.values()) - min_y + frame_h
    assert (canvas.shape[1], canvas.shape[0]) == (expected_w, expected_h)
    for (ix, iy), (px, py) in offsets.items():
        x, y = px - min_x, py - min_y
        window = world[
            base_y + py : base_y + py + frame_h,
            base_x + px : base_x + px + frame_w,
        ]
        assert np.array_equal(canvas[y : y + frame_h, x : x + frame_w], window), (ix, iy)
