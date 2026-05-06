"""Compare an exported crop with the **same bbox rectangle** on another frame (1:1, no search).

Uses the same pixel rounding as :func:`ui.area_annotator.crop_region` / crop export.
The live frame must be the **same resolution** as when the crop was produced.
"""

from __future__ import annotations

import math
from typing import TypedDict

import cv2
import numpy as np


class TemplateMatchResult(TypedDict):
    # TM_CCOEFF_NORMED (1:1 bbox patch vs template yields one coefficient).
    score: float
    # Global top-left (x, y); crop rounding matches labeling export.
    top_left: tuple[int, int]


def patch_bgr_from_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
) -> tuple[np.ndarray, tuple[int, int]]:
    """Cut out the bbox rectangle in pixels (percent of frame); mirrors labeling crop rounding."""
    if image_bgr.ndim != 3:
        raise ValueError("Expected HxWx3 BGR image.")
    hi, wi = image_bgr.shape[:2]

    left = bbox_percent["x"] / 100.0 * wi
    top = bbox_percent["y"] / 100.0 * hi
    width = bbox_percent["width"] / 100.0 * wi
    height = bbox_percent["height"] / 100.0 * hi

    L = int(math.floor(left))
    T = int(math.floor(top))
    R = int(math.ceil(left + width))
    B = int(math.ceil(top + height))

    L = max(0, min(L, wi - 1))
    T = max(0, min(T, hi - 1))
    R = max(L + 1, min(R, wi))
    B = max(T + 1, min(B, hi))

    return image_bgr[T:B, L:R].copy(), (L, T)


def match_crop_1to1_at_bbox_percent(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    bbox_percent: dict[str, float],
) -> TemplateMatchResult:
    """Compare bbox patch to ``template_bgr`` pixel-wise (same shape only).

    No margin/pyramid: template must match what labeling exported from this bbox.
    """
    if template_bgr.ndim != 3:
        raise ValueError("Expected HxWx3 BGR template.")
    patch, (L, T) = patch_bgr_from_bbox_percent(image_bgr, bbox_percent)

    if patch.shape != template_bgr.shape:
        raise ValueError(
            f"1:1 shape mismatch: bbox patch {patch.shape} vs template {template_bgr.shape}. "
            "Use the same frame size as when exporting references/crop."
        )

    pg = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    score = float(cv2.matchTemplate(pg, tg, cv2.TM_CCOEFF_NORMED)[0, 0])
    return TemplateMatchResult(score=score, top_left=(L, T))


def match_template_in_search_roi_bbox_percent(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    search_bbox_percent: dict[str, float],
) -> TemplateMatchResult:
    """Slide ``template_bgr`` inside ROI from ``search_bbox_percent``.

    Returns best TM_CCOEFF_NORMED and global top-left on ``image_bgr``.
    """
    if template_bgr.ndim != 3:
        raise ValueError("Expected HxWx3 BGR template.")
    roi, (L, T) = patch_bgr_from_bbox_percent(image_bgr, search_bbox_percent)
    rh, rw = roi.shape[:2]
    th, tw = template_bgr.shape[:2]
    if th > rh or tw > rw or th < 1 or tw < 1:
        raise ValueError(
            f"Template {tw}×{th} must fit inside search ROI {rw}×{rh} "
            "(draw a larger **search_region** in Labeling)."
        )

    rg = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    heat = cv2.matchTemplate(rg, tg, cv2.TM_CCOEFF_NORMED)
    _mn, max_val, _mn_loc, max_loc = cv2.minMaxLoc(heat)
    x_off, y_off = max_loc
    gx = int(L + x_off)
    gy = int(T + y_off)
    return TemplateMatchResult(score=float(max_val), top_left=(gx, gy))
