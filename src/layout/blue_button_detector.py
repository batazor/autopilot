"""Detect blue CTA buttons via a local HSV component mask."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from layout.template_match import patch_bgr_from_bbox_percent

BLUE_HUE_MIN = 95
BLUE_HUE_MAX = 125
BLUE_MIN_SATURATION = 35
BLUE_MIN_VALUE = 95


@dataclass(frozen=True)
class BlueButtonHit:
    present: bool
    score: float
    fill_ratio: float
    bbox_percent: dict[str, float]
    top_left: tuple[int, int]
    width: int
    height: int


@dataclass(frozen=True)
class _BlueButtonCandidate:
    score: float
    fill_ratio: float
    top_left: tuple[int, int]
    width: int
    height: int


def _bbox_px_from_percent(
    bbox: dict[str, Any],
    *,
    frame_w: int,
    frame_h: int,
) -> tuple[int, int, int, int]:
    x = int(round(float(bbox["x"]) / 100.0 * frame_w))
    y = int(round(float(bbox["y"]) / 100.0 * frame_h))
    w = int(round(float(bbox["width"]) / 100.0 * frame_w))
    h = int(round(float(bbox["height"]) / 100.0 * frame_h))
    return x, y, max(0, w), max(0, h)


def _expanded_anchor_patch(
    image_bgr: np.ndarray,
    anchor_bbox_percent: dict[str, Any],
    *,
    x_padding_ratio: float,
    y_padding_ratio: float,
) -> tuple[np.ndarray, tuple[int, int]]:
    frame_h, frame_w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    ax, ay, aw, ah = _bbox_px_from_percent(
        anchor_bbox_percent,
        frame_w=frame_w,
        frame_h=frame_h,
    )
    if aw <= 0 or ah <= 0:
        return image_bgr[0:0, 0:0], (0, 0)
    x1 = max(0, int(round(ax - aw * float(x_padding_ratio))))
    y1 = max(0, int(round(ay - ah * float(y_padding_ratio))))
    x2 = min(frame_w, int(round(ax + aw * (1.0 + float(x_padding_ratio)))))
    y2 = min(frame_h, int(round(ay + ah * (1.0 + float(y_padding_ratio)))))
    return image_bgr[y1:y2, x1:x2], (x1, y1)


def find_blue_buttons(
    image_bgr: np.ndarray,
    *,
    anchor_bbox_percent: dict[str, Any],
    search_bbox_percent: dict[str, Any] | None = None,
    min_score: float = 0.5,
    min_fill_ratio: float = 0.30,
    x_padding_ratio: float = 0.50,
    y_padding_ratio: float = 1.00,
    x_center_tolerance_ratio: float = 1.20,
    y_center_tolerance_ratio: float = 2.00,
    min_width_ratio: float = 0.35,
    max_width_ratio: float = 3.40,
    min_height_ratio: float = 0.25,
    max_height_ratio: float = 3.40,
) -> list[BlueButtonHit]:
    """Find blue CTA-like components near an anchor bbox.

    Building panels contain large blue-tinted backgrounds, so unlike the green
    CTA detector this intentionally searches a local patch around the anchor by
    default. A caller can still pass ``search_bbox_percent`` for an explicit ROI.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return []
    if not (
        isinstance(anchor_bbox_percent, dict)
        and all(k in anchor_bbox_percent for k in ("x", "y", "width", "height"))
    ):
        return []

    frame_h, frame_w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    ax, ay, aw, ah = _bbox_px_from_percent(
        anchor_bbox_percent,
        frame_w=frame_w,
        frame_h=frame_h,
    )
    if aw <= 0 or ah <= 0:
        return []
    anchor_area = float(aw * ah)

    if isinstance(search_bbox_percent, dict):
        search, (search_x, search_y) = patch_bgr_from_bbox_percent(
            image_bgr,
            search_bbox_percent,
        )
    else:
        search, (search_x, search_y) = _expanded_anchor_patch(
            image_bgr,
            anchor_bbox_percent,
            x_padding_ratio=x_padding_ratio,
            y_padding_ratio=y_padding_ratio,
        )
    if search.size == 0:
        return []

    hsv = cv2.cvtColor(search, cv2.COLOR_BGR2HSV)
    mask = (
        (hsv[..., 0] >= BLUE_HUE_MIN)
        & (hsv[..., 0] <= BLUE_HUE_MAX)
        & (hsv[..., 1] >= BLUE_MIN_SATURATION)
        & (hsv[..., 2] >= BLUE_MIN_VALUE)
    ).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    candidates: list[_BlueButtonCandidate] = []
    min_w = aw * float(min_width_ratio)
    max_w = aw * float(max_width_ratio)
    min_h = ah * float(min_height_ratio)
    max_h = ah * float(max_height_ratio)
    anchor_cx = ax + aw / 2.0
    anchor_cy = ay + ah / 2.0
    max_dx = aw * float(x_center_tolerance_ratio)
    max_dy = ah * float(y_center_tolerance_ratio)

    for idx in range(1, count):
        x, y, w, h, area = [int(v) for v in stats[idx]]
        if w <= 0 or h <= 0 or area <= 0:
            continue
        gx = x + search_x
        gy = y + search_y
        cx = float(centroids[idx][0]) + search_x
        cy = float(centroids[idx][1]) + search_y
        if not (min_w <= w <= max_w and min_h <= h <= max_h):
            continue
        if abs(cx - anchor_cx) > max_dx or abs(cy - anchor_cy) > max_dy:
            continue
        fill_ratio = float(area) / float(w * h)
        if fill_ratio < float(min_fill_ratio):
            continue
        score = float(area) / anchor_area
        if score < float(min_score):
            continue
        candidates.append(
            _BlueButtonCandidate(
                score,
                fill_ratio,
                (int(gx), int(gy)),
                int(w),
                int(h),
            )
        )

    out: list[BlueButtonHit] = []
    for item in sorted(candidates, key=lambda c: (-c.score, c.top_left[1], c.top_left[0])):
        gx, gy = item.top_left
        bbox_percent = {
            "x": 100.0 * float(gx) / float(frame_w),
            "y": 100.0 * float(gy) / float(frame_h),
            "width": 100.0 * float(item.width) / float(frame_w),
            "height": 100.0 * float(item.height) / float(frame_h),
        }
        out.append(
            BlueButtonHit(
                True,
                item.score,
                item.fill_ratio,
                bbox_percent,
                item.top_left,
                item.width,
                item.height,
            )
        )
    return out


def detect_blue_button(
    image_bgr: np.ndarray,
    anchor_bbox_percent: dict[str, Any],
    **kwargs: Any,
) -> BlueButtonHit:
    """Return the strongest local blue CTA-like component near an anchor."""
    hits = find_blue_buttons(
        image_bgr,
        anchor_bbox_percent=anchor_bbox_percent,
        **kwargs,
    )
    if not hits:
        return BlueButtonHit(False, 0.0, 0.0, {}, (0, 0), 0, 0)
    return hits[0]
