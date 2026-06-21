from __future__ import annotations

from typing import Literal

import cv2
import numpy as np

ColorLabel = Literal["red", "blue", "green", "gray"]


def dominant_color_label_bgr(patch_bgr: np.ndarray) -> tuple[ColorLabel, dict[str, float]]:
    """Return (dominant_label, share_map) for a BGR patch.

    Labels: red/blue/green/gray.

    Heuristic (HSV):
    - gray: low saturation or very dark pixels
    - red/green/blue: hue ranges on remaining pixels
    - "other" hues (yellow/cyan/purple): assigned to nearest primary by hue
    """
    if patch_bgr.size <= 0:
        return "gray", {"red": 0.0, "blue": 0.0, "green": 0.0, "gray": 1.0}
    try:
        hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    except Exception:
        return "gray", {"red": 0.0, "blue": 0.0, "green": 0.0, "gray": 1.0}

    h = hsv[:, :, 0].astype(np.int16, copy=False)  # 0..179
    s = hsv[:, :, 1].astype(np.int16, copy=False)  # 0..255
    v = hsv[:, :, 2].astype(np.int16, copy=False)  # 0..255

    gray_mask = (s < 40) | (v < 40)
    color_mask = ~gray_mask

    red_mask = color_mask & ((h <= 10) | (h >= 170))
    green_mask = color_mask & (h >= 35) & (h <= 85)
    blue_mask = color_mask & (h >= 90) & (h <= 140)

    other_mask = color_mask & ~(red_mask | green_mask | blue_mask)
    other_red = other_blue = other_green = 0
    if np.any(other_mask):
        ho = h[other_mask]
        d_red = np.minimum(ho, 180 - ho)  # wrap-around
        d_green = np.abs(ho - 60)
        d_blue = np.abs(ho - 120)
        idx = np.stack([d_red, d_blue, d_green], axis=0).argmin(axis=0)
        other_red = int(np.sum(idx == 0))
        other_blue = int(np.sum(idx == 1))
        other_green = int(np.sum(idx == 2))

    n = int(h.size) or 1
    c_red = int(np.count_nonzero(red_mask)) + other_red
    c_blue = int(np.count_nonzero(blue_mask)) + other_blue
    c_green = int(np.count_nonzero(green_mask)) + other_green
    c_gray = int(np.count_nonzero(gray_mask))
    shares = {
        "red": c_red / n,
        "blue": c_blue / n,
        "green": c_green / n,
        "gray": c_gray / n,
    }
    dominant: ColorLabel = max(shares.items(), key=lambda kv: kv[1])[0]  # type: ignore[assignment]  # ty: ignore[invalid-assignment]
    return dominant, shares

