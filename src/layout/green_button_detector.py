"""Detect broad green CTA buttons via an HSV component mask."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

from layout.template_match import patch_bgr_from_bbox_percent

GREEN_HUE_MIN = 35
GREEN_HUE_MAX = 90
GREEN_MIN_SATURATION = 50
GREEN_MIN_VALUE = 90


@dataclass(frozen=True)
class GreenButtonHit:
    present: bool
    score: float
    fill_ratio: float
    bbox_percent: dict[str, float]
    top_left: tuple[int, int]
    width: int
    height: int


@dataclass(frozen=True)
class _GreenButtonCandidate:
    score: float
    fill_ratio: float
    top_left: tuple[int, int]
    width: int
    height: int
    center: tuple[float, float]


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


def find_green_buttons(
    image_bgr: np.ndarray,
    *,
    anchor_bbox_percent: dict[str, Any] | None = None,
    search_bbox_percent: dict[str, Any] | None = None,
    min_score: float = 0.35,
    min_fill_ratio: float = 0.45,
    x_tolerance_ratio: float = 0.65,
    min_width_ratio: float = 0.50,
    max_width_ratio: float = 1.45,
    min_height_ratio: float = 0.30,
    max_height_ratio: float = 1.35,
) -> list[GreenButtonHit]:
    """Find green CTA-like components.

    When ``anchor_bbox_percent`` is supplied, it defines the expected button
    size and x-column while leaving y dynamic. Without an anchor the detector
    returns every broad green CTA in the search area.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return []

    frame_h, frame_w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    ax = 0
    aw = frame_w
    ah = max(1, int(round(frame_h * 0.05)))
    anchor_area = float(aw * ah)
    has_anchor = isinstance(anchor_bbox_percent, dict) and all(
        k in anchor_bbox_percent for k in ("x", "y", "width", "height")
    )
    if has_anchor:
        ax, _ay, aw, ah = _bbox_px_from_percent(
            anchor_bbox_percent,
            frame_w=frame_w,
            frame_h=frame_h,
        )
        if aw <= 0 or ah <= 0:
            return []
        anchor_area = float(aw * ah)

    search_x = 0
    search_y = 0
    search = image_bgr
    if isinstance(search_bbox_percent, dict):
        search, (search_x, search_y) = patch_bgr_from_bbox_percent(
            image_bgr,
            search_bbox_percent,
        )
        if search.size == 0:
            return []

    hsv = cv2.cvtColor(search, cv2.COLOR_BGR2HSV)
    mask = (
        (hsv[..., 0] >= GREEN_HUE_MIN)
        & (hsv[..., 0] <= GREEN_HUE_MAX)
        & (hsv[..., 1] >= GREEN_MIN_SATURATION)
        & (hsv[..., 2] >= GREEN_MIN_VALUE)
    ).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (17, 9))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    candidates: list[_GreenButtonCandidate] = []
    min_w = aw * float(min_width_ratio)
    max_w = aw * float(max_width_ratio)
    min_h = ah * float(min_height_ratio)
    max_h = ah * float(max_height_ratio)
    min_cx = ax - aw * float(x_tolerance_ratio)
    max_cx = ax + aw * (1.0 + float(x_tolerance_ratio))
    anchor_area = float(aw * ah)

    for idx in range(1, count):
        x, y, w, h, area = [int(v) for v in stats[idx]]
        if w <= 0 or h <= 0 or area <= 0:
            continue
        gx = x + search_x
        gy = y + search_y
        cx = float(centroids[idx][0]) + search_x
        cy = float(centroids[idx][1]) + search_y
        if has_anchor and not (min_cx <= cx <= max_cx):
            continue
        if has_anchor and not (min_w <= w <= max_w and min_h <= h <= max_h):
            continue
        if not has_anchor and not (70 <= w <= 260 and 20 <= h <= 90):
            continue
        fill_ratio = float(area) / float(w * h)
        if fill_ratio < float(min_fill_ratio):
            continue
        score = float(area) / anchor_area if has_anchor else fill_ratio
        if score < float(min_score):
            continue
        candidates.append(
            _GreenButtonCandidate(
                score,
                fill_ratio,
                (int(gx), int(gy)),
                int(w),
                int(h),
                (cx, cy),
            )
        )

    out: list[GreenButtonHit] = []
    for item in sorted(candidates, key=lambda c: (-c.score, c.top_left[1], c.top_left[0])):
        gx, gy = item.top_left
        bbox_percent = {
            "x": 100.0 * float(gx) / float(frame_w),
            "y": 100.0 * float(gy) / float(frame_h),
            "width": 100.0 * float(item.width) / float(frame_w),
            "height": 100.0 * float(item.height) / float(frame_h),
        }
        out.append(
            GreenButtonHit(
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


def detect_green_button(
    image_bgr: np.ndarray,
    anchor_bbox_percent: dict[str, Any],
    **kwargs: Any,
) -> GreenButtonHit:
    """Return the strongest green CTA-like component near an anchor column."""
    hits = find_green_buttons(
        image_bgr,
        anchor_bbox_percent=anchor_bbox_percent,
        **kwargs,
    )
    if not hits:
        return GreenButtonHit(False, 0.0, 0.0, {}, (0, 0), 0, 0)
    return hits[0]
