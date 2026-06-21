"""Normalize ADB framebuffer captures to the bot coordinate space (720×1280 BGR)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

from layout.types import Point

logger = logging.getLogger(__name__)

GAME_FRAME_SIZE = (720, 1280)
_MIN_BAR_COLS = 6
_MIN_BAR_ROWS = 8
_BAR_MAX_BRIGHTNESS = 14
_CONTENT_ROW_STD_MIN = 4.0
# Dithered letterbox edges have std just above 4 but stay very dark (mean ~28–40).
_CONTENT_ROW_MEAN_MIN_TOP = 38.0
_CONTENT_ROW_MEAN_MIN_BOTTOM = 45.0
# Keep a little extra below the detected top edge (game header / status transition).
_VERTICAL_TOP_RECOVER_PX = 18


@dataclass(frozen=True, slots=True)
class FrameNormalizeTransform:
    """Geometry used by ``normalize_adb_frame_bgr`` (aspect-preserving cover crop)."""

    source_size: tuple[int, int]
    target_size: tuple[int, int]
    crop_left: int
    crop_top: int
    crop_size: tuple[int, int]
    scale_x: float
    scale_y: float
    resized_crop_left: int = 0
    resized_crop_top: int = 0

    def normalized_to_source_point(self, point: Point) -> Point:
        sw, sh = self.source_size
        x = self.crop_left + int(round((float(point.x) + self.resized_crop_left) / self.scale_x))
        y = self.crop_top + int(round((float(point.y) + self.resized_crop_top) / self.scale_y))
        return Point(
            max(0, min(sw - 1, x)),
            max(0, min(sh - 1, y)),
        )


def _active_content_rows(
    gray: np.ndarray,
    *,
    mean_min: float,
) -> np.ndarray:
    """Rows that belong to the game framebuffer, not uniform letterbox strips."""
    row_std = gray.std(axis=1)
    row_mean = gray.mean(axis=1)
    return (row_std > _CONTENT_ROW_STD_MIN) & (row_mean > mean_min)


def crop_vertical_letterbox_bgr(image: np.ndarray) -> np.ndarray:
    """Remove top/bottom letterboxing (status-bar pad, blue/black bars)."""
    if image is None or image.size == 0:
        return image
    h, w = image.shape[:2]
    if w < 32 or h < 32:
        return image

    top, bottom_exclusive = _vertical_letterbox_bounds(image)
    if top == 0 and bottom_exclusive == h:
        return image

    logger.debug(
        "Vertical letterbox crop: removed top=%d bottom=%d px from %dx%d frame",
        top,
        h - bottom_exclusive,
        w,
        h,
    )
    return image[top:bottom_exclusive, :].copy()


def _vertical_letterbox_bounds(image: np.ndarray) -> tuple[int, int]:
    h, _w = image.shape[:2]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    active_top = _active_content_rows(gray, mean_min=_CONTENT_ROW_MEAN_MIN_TOP)
    active_bottom = _active_content_rows(gray, mean_min=_CONTENT_ROW_MEAN_MIN_BOTTOM)
    if not active_top.any():
        active_top = gray.std(axis=1) > _CONTENT_ROW_STD_MIN
    if not active_bottom.any():
        active_bottom = gray.std(axis=1) > _CONTENT_ROW_STD_MIN
    if not active_top.any() or not active_bottom.any():
        return 0, h

    top_detected = int(np.argmax(active_top))
    top = top_detected
    for recover in range(1, _VERTICAL_TOP_RECOVER_PX + 1):
        candidate = top_detected - recover
        if candidate < 0:
            break
        row_std = float(gray[candidate].std())
        row_mean = float(gray[candidate].mean())
        if row_std <= 0.25 and row_mean < 48.0:
            break
        top = candidate
    bottom = int(len(active_bottom) - 1 - np.argmax(active_bottom[::-1]))
    content_h = bottom - top + 1
    if content_h <= 0 or content_h >= h:
        return 0, h

    bar_top = top
    bar_bottom = h - bottom - 1
    if bar_top < _MIN_BAR_ROWS and bar_bottom < _MIN_BAR_ROWS:
        return 0, h

    return top, bottom + 1


def crop_horizontal_letterbox_bgr(image: np.ndarray) -> np.ndarray:
    """Remove side pillarboxing (black bars) when present."""
    if image is None or image.size == 0:
        return image
    h, w = image.shape[:2]
    if w < 32 or h < 32:
        return image

    left, right_exclusive = _horizontal_letterbox_bounds(image)
    if left == 0 and right_exclusive == w:
        return image

    logger.debug(
        "Letterbox crop: removed left=%d right=%d px from %dx%d frame",
        left,
        w - right_exclusive,
        w,
        h,
    )
    return image[:, left:right_exclusive].copy()


def _horizontal_letterbox_bounds(image: np.ndarray) -> tuple[int, int]:
    _h, w = image.shape[:2]

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    col_max = gray.max(axis=0)
    active = col_max > _BAR_MAX_BRIGHTNESS
    if not active.any():
        return 0, w

    left = int(np.argmax(active))
    right = int(len(active) - 1 - np.argmax(active[::-1]))
    content_w = right - left + 1
    if content_w <= 0 or content_w >= w:
        return 0, w

    bar_left = left
    bar_right = w - right - 1
    if bar_left < _MIN_BAR_COLS and bar_right < _MIN_BAR_COLS:
        return 0, w

    return left, right + 1


def normalize_adb_frame_bgr(
    image: np.ndarray,
    *,
    target_size: tuple[int, int] = GAME_FRAME_SIZE,
) -> np.ndarray:
    """Crop letterbox bars, then cover-fit to ``target_size`` without aspect stretch."""
    normalized, _transform = normalize_adb_frame_bgr_with_transform(
        image,
        target_size=target_size,
    )
    return normalized


def normalize_adb_frame_bgr_with_transform(
    image: np.ndarray,
    *,
    target_size: tuple[int, int] = GAME_FRAME_SIZE,
) -> tuple[np.ndarray, FrameNormalizeTransform | None]:
    """Normalize a frame and return the exact reverse mapping geometry."""
    if image is None or image.size == 0:
        return image, None

    source_h, source_w = image.shape[:2]
    tw, th = target_size
    tw_i, th_i = max(1, int(tw)), max(1, int(th))

    # Fast path: device is already in the target coordinate space (typically
    # because the user set ``wm size`` to match the bot's 720x1280 grid).
    # Running letterbox detection here is unsafe — dimmed-modal screens
    # (offline-income overlay, popup dim curtain, etc.) have dark low-variance
    # rows at the top and bottom that the heuristic misclassifies as bars,
    # producing an over-cropped + zoomed frame. When source already matches
    # target there is nothing to remove, so return the raw frame as-is.
    if source_w == tw_i and source_h == th_i:
        return image, FrameNormalizeTransform(
            source_size=(source_w, source_h),
            target_size=(tw_i, th_i),
            crop_left=0,
            crop_top=0,
            crop_size=(source_w, source_h),
            scale_x=1.0,
            scale_y=1.0,
            resized_crop_left=0,
            resized_crop_top=0,
        )

    crop_top, crop_bottom = _vertical_letterbox_bounds(image)
    cropped = image[crop_top:crop_bottom, :]
    crop_left, crop_right = _horizontal_letterbox_bounds(cropped)
    cropped = cropped[:, crop_left:crop_right]

    h, w = cropped.shape[:2]
    if w <= 0 or h <= 0:
        return cropped, None

    scale = max(float(tw) / float(w), float(th) / float(h))
    resized_w = max(1, int(round(w * scale)))
    resized_h = max(1, int(round(h * scale)))
    crop_resized_left = max(0, (resized_w - int(tw)) // 2)
    crop_resized_top = (
        max(0, int((resized_h - int(th)) * 0.35))
        if resized_h > int(th)
        else max(0, (resized_h - int(th)) // 2)
    )

    transform = FrameNormalizeTransform(
        source_size=(source_w, source_h),
        target_size=(max(1, int(tw)), max(1, int(th))),
        crop_left=crop_left,
        crop_top=crop_top,
        crop_size=(w, h),
        scale_x=scale,
        scale_y=scale,
        resized_crop_left=crop_resized_left,
        resized_crop_top=crop_resized_top,
    )
    if w == tw and h == th:
        return cropped, transform

    resized = cv2.resize(cropped, (resized_w, resized_h), interpolation=cv2.INTER_AREA)
    return (
        resized[
            crop_resized_top : crop_resized_top + int(th),
            crop_resized_left : crop_resized_left + int(tw),
        ],
        transform,
    )


def frame_normalize_transform_for_size(
    source_size: tuple[int, int],
    *,
    target_size: tuple[int, int] = GAME_FRAME_SIZE,
) -> FrameNormalizeTransform:
    """Return the cover-fit transform from raw frame size to bot frame size."""

    sw = max(1, int(source_size[0]))
    sh = max(1, int(source_size[1]))
    tw = max(1, int(target_size[0]))
    th = max(1, int(target_size[1]))
    scale = max(tw / float(sw), th / float(sh))
    resized_w = max(1, int(round(sw * scale)))
    resized_h = max(1, int(round(sh * scale)))
    crop_resized_left = max(0, (resized_w - tw) // 2)
    crop_resized_top = (
        max(0, int((resized_h - th) * 0.35))
        if resized_h > th
        else max(0, (resized_h - th) // 2)
    )
    return FrameNormalizeTransform(
        source_size=(sw, sh),
        target_size=(tw, th),
        crop_left=0,
        crop_top=0,
        crop_size=(sw, sh),
        scale_x=scale,
        scale_y=scale,
        resized_crop_left=crop_resized_left,
        resized_crop_top=crop_resized_top,
    )


def normalized_point_to_source_point(
    point: Point,
    source_size: tuple[int, int],
    *,
    target_size: tuple[int, int] = GAME_FRAME_SIZE,
) -> Point:
    """Map a bot-frame point back to the raw ADB touch coordinate space."""

    return frame_normalize_transform_for_size(
        source_size,
        target_size=target_size,
    ).normalized_to_source_point(point)


def wm_size_for_physical(
    physical_w: int,
    physical_h: int,
    *,
    target_width: int = 720,
) -> str:
    """Pick ``wm size`` that preserves the physical aspect ratio at ``target_width``."""
    pw = max(1, int(physical_w))
    ph = max(1, int(physical_h))
    tw = max(1, int(target_width))
    th = max(1, int(round(tw * ph / pw)))
    return f"{tw}x{th}"
