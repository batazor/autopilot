from __future__ import annotations

import numpy as np

from adb.frame_normalize import (
    _VERTICAL_TOP_RECOVER_PX,
    crop_horizontal_letterbox_bgr,
    crop_vertical_letterbox_bgr,
    normalize_adb_frame_bgr,
    normalize_adb_frame_bgr_with_transform,
    normalized_point_to_source_point,
    wm_size_for_physical,
)
from layout.types import Point


def test_wm_size_for_physical_preserves_aspect() -> None:
    assert wm_size_for_physical(1080, 2400) == "720x1600"


def test_crop_vertical_letterbox() -> None:
    img = np.zeros((1600, 720, 3), dtype=np.uint8)
    img[:220, :] = (27, 27, 80)  # uniform blue top bar
    img[-130:, :] = (27, 27, 80)  # uniform blue bottom bar
    rng = np.random.default_rng(0)
    img[220:-130, :] = np.clip(
        160 + rng.integers(-30, 30, (1600 - 220 - 130, 720, 3)), 0, 255
    ).astype(np.uint8)
    cropped = crop_vertical_letterbox_bgr(img)
    assert cropped.shape[0] <= 1600 - 220 - 130 + _VERTICAL_TOP_RECOVER_PX
    assert cropped.shape[0] >= 1200


def test_crop_vertical_letterbox_skips_when_no_bars() -> None:
    img = np.full((1280, 720, 3), 120, dtype=np.uint8)
    cropped = crop_vertical_letterbox_bgr(img)
    assert cropped.shape == img.shape


def test_crop_horizontal_letterbox() -> None:
    img = np.zeros((1280, 720, 3), dtype=np.uint8)
    img[:, 80:640] = 180
    cropped = crop_horizontal_letterbox_bgr(img)
    assert cropped.shape[1] == 560


def test_normalize_adb_frame_stretches_tall_capture() -> None:
    img = np.full((1600, 720, 3), 200, dtype=np.uint8)
    out = normalize_adb_frame_bgr(img)
    assert out.shape[:2] == (1280, 720)


def test_normalize_adb_frame_noop_when_already_target() -> None:
    img = np.full((1280, 720, 3), 120, dtype=np.uint8)
    out = normalize_adb_frame_bgr(img)
    assert out.shape[:2] == (1280, 720)


def test_normalize_adb_frame_keeps_dimmed_modal_frame_intact() -> None:
    """720x1280 captures with a dim overlay must not trigger letterbox detection.

    Modal screens (offline-income overlay, popup dim curtain) have dark
    low-variance rows at top/bottom that the letterbox heuristic previously
    misclassified as bars — over-cropping ~280px from the top and zooming the
    middle band back to 720x1280. Once ``wm size`` matches the bot's coordinate
    space, normalization must be a no-op.
    """
    rng = np.random.default_rng(7)
    img = np.full((1280, 720, 3), 30, dtype=np.uint8)
    img[300:1100, :] = np.clip(
        160 + rng.integers(-30, 30, (800, 720, 3)), 0, 255
    ).astype(np.uint8)
    out, transform = normalize_adb_frame_bgr_with_transform(img)
    assert out.shape[:2] == (1280, 720)
    assert transform is not None
    assert transform.crop_top == 0
    assert transform.crop_left == 0
    assert transform.scale_x == 1.0
    assert transform.scale_y == 1.0
    # The bright middle band must land at the same rows as in the input — a
    # bug would have re-zoomed it into the centre rows.
    assert float(out[500].mean()) > 100.0
    assert float(out[50].mean()) < 50.0
    assert float(out[1200].mean()) < 50.0


def test_normalize_adb_frame_phone_aspect_no_black_padding() -> None:
    """9:20 phone capture (720×1600) must not pad black bars after blue-bar crop."""
    img = np.zeros((1600, 720, 3), dtype=np.uint8)
    img[:220, :] = (27, 27, 80)
    img[-130:, :] = (27, 27, 80)
    rng = np.random.default_rng(1)
    img[220:-130, :] = np.clip(
        160 + rng.integers(-30, 30, (1600 - 220 - 130, 720, 3)), 0, 255
    ).astype(np.uint8)
    out = normalize_adb_frame_bgr(img)
    assert out.shape[:2] == (1280, 720)
    assert float(out[0].mean()) > 45.0
    assert float(out[-1].mean()) > 45.0


def test_normalized_point_to_source_inverts_cover_crop() -> None:
    assert normalized_point_to_source_point(Point(360, 640), (720, 1600)) == Point(360, 752)
    assert normalized_point_to_source_point(Point(10, 10), (720, 1600)) == Point(10, 122)


def test_normalize_transform_inverts_letterbox_crop() -> None:
    img = np.zeros((1600, 800, 3), dtype=np.uint8)
    rng = np.random.default_rng(2)
    img[220:-130, 40:760] = np.clip(
        160 + rng.integers(-30, 30, (1600 - 220 - 130, 720, 3)), 0, 255
    ).astype(np.uint8)

    out, transform = normalize_adb_frame_bgr_with_transform(img)

    assert out.shape[:2] == (1280, 720)
    assert transform is not None
    assert transform.source_size == (800, 1600)
    assert transform.crop_left == 40
    assert transform.crop_size[0] == 720
    assert transform.normalized_to_source_point(Point(0, 0)) == Point(
        transform.crop_left + round(transform.resized_crop_left / transform.scale_x),
        transform.crop_top + round(transform.resized_crop_top / transform.scale_y),
    )
    assert transform.normalized_to_source_point(Point(360, 640)) == Point(
        transform.crop_left + round((360 + transform.resized_crop_left) / transform.scale_x),
        transform.crop_top + round((640 + transform.resized_crop_top) / transform.scale_y),
    )
