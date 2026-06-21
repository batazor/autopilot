from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from layout.template_match import patch_bgr_from_bbox_percent

RibbonKind = Literal["any", "blue", "orange"]


@dataclass(frozen=True)
class RewardRibbonStats:
    present: bool
    kind: RibbonKind
    mask_share: float
    component_width_ratio: float
    component_y_ratio: float
    component_height_ratio: float
    component_area_ratio: float
    component_bbox: tuple[int, int, int, int]


def detect_reward_ribbon_in_bbox_percent(
    image_bgr: np.ndarray,
    bbox: dict[str, float],
    *,
    kind: RibbonKind = "any",
    min_mask_share: float = 0.15,
    min_component_width_ratio: float = 0.55,
    min_component_y_ratio: float = 0.0,
    min_component_height_ratio: float = 0.25,
    min_component_area_ratio: float = 0.12,
) -> RewardRibbonStats:
    """Detect the wide reward-title ribbon shape in a broad top-screen band.

    The WOS rewards popups reuse a banner with side tails: blue for normal /
    claimed / chapter rewards and orange for upgraded rewards. Text and snow
    particles vary, so this checks saturated ribbon geometry instead of glyphs.
    """
    patch, _tl = patch_bgr_from_bbox_percent(image_bgr, bbox)
    ph, pw = int(patch.shape[0]), int(patch.shape[1])
    if ph <= 0 or pw <= 0:
        return RewardRibbonStats(
            False, kind, 0.0, 0.0, 0.0, 0.0, 0.0, (0, 0, 0, 0)
        )

    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    blue = (h >= 85) & (h <= 110) & (s >= 70) & (v >= 120)
    orange = (h >= 5) & (h <= 30) & (s >= 80) & (v >= 120)
    if kind == "blue":
        mask_bool = blue
    elif kind == "orange":
        mask_bool = orange
    else:
        mask_bool = blue | orange

    mask = mask_bool.astype(np.uint8) * 255
    if mask.size:
        kernel = np.ones((7, 7), dtype=np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
    mask_share = float(np.count_nonzero(mask)) / float(mask.size or 1)

    contours, _hier = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best_bbox = (0, 0, 0, 0)
    best_area = 0.0
    for cnt in contours:
        x, y, w, hgt = cv2.boundingRect(cnt)
        area = float(cv2.contourArea(cnt))
        if area > best_area:
            best_area = area
            best_bbox = (int(x), int(y), int(w), int(hgt))

    _x, by, bw, bh = best_bbox
    width_ratio = float(bw) / float(pw)
    y_ratio = float(by) / float(ph)
    height_ratio = float(bh) / float(ph)
    area_ratio = best_area / float(pw * ph)
    present = (
        mask_share >= min_mask_share
        and width_ratio >= min_component_width_ratio
        and y_ratio >= min_component_y_ratio
        and height_ratio >= min_component_height_ratio
        and area_ratio >= min_component_area_ratio
    )
    return RewardRibbonStats(
        bool(present),
        kind,
        mask_share,
        width_ratio,
        y_ratio,
        height_ratio,
        area_ratio,
        best_bbox,
    )
