"""Programmatic "active tab" detector.

Mail / event UIs use a tab strip where the selected tab is drawn with a light
cream / white background while inactive tabs use the saturated blue base. The
difference shows up cleanly in HSV statistics over the labeled tab bbox:

* active tab — mean saturation drops (cream is near-grey in HSV);
* active tab — mean value rises (background goes from mid-blue to near-white).

Calibration on ``references/mail_page.png`` (System tab active):

* inactive tabs: ``S_mean ≈ 160-180``, ``V_mean ≈ 162-178``;
* active tab:    ``S_mean ≈ 84``,     ``V_mean ≈ 202``.

The defaults below sit in the middle of that gap with comfortable headroom.
"""
from __future__ import annotations

import cv2
import numpy as np

from layout.template_match import patch_bgr_from_bbox_percent

TAB_ACTIVE_MAX_MEAN_SATURATION = 120.0
"""Mean HSV saturation must be **below** this to call the tab active."""

TAB_ACTIVE_MIN_MEAN_VALUE = 180.0
"""Mean HSV value must be **above** this to call the tab active."""


def tab_activity_stats(patch_bgr: np.ndarray) -> tuple[float, float]:
    """Return ``(mean_saturation, mean_value)`` for ``patch_bgr`` (HSV)."""
    if patch_bgr is None or patch_bgr.ndim != 3 or patch_bgr.size == 0:
        return 0.0, 0.0
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()), float(hsv[..., 2].mean())


def is_tab_active_in_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
    *,
    max_mean_saturation: float = TAB_ACTIVE_MAX_MEAN_SATURATION,
    min_mean_value: float = TAB_ACTIVE_MIN_MEAN_VALUE,
) -> bool:
    """Return ``True`` iff the labeled tab bbox shows the active (light) background.

    Both gates must pass: low saturation **and** high value. Either alone is
    not enough — bright white text on a saturated blue tab still drives V high
    while S stays high (rejecting it correctly), and a desaturated dark patch
    could trick S alone (rejected by the V floor).
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return False
    if not isinstance(bbox_percent, dict):
        return False
    if not all(k in bbox_percent for k in ("x", "y", "width", "height")):
        return False

    patch, _ = patch_bgr_from_bbox_percent(image_bgr, bbox_percent)
    if patch.size == 0:
        return False
    mean_s, mean_v = tab_activity_stats(patch)
    return mean_s < float(max_mean_saturation) and mean_v > float(min_mean_value)
