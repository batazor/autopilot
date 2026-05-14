"""Map percent-based bbox (labeling / ``area.json``) to device pixel taps."""

from __future__ import annotations

import random

from layout.types import Point


def bbox_percent_center_xy_pct(bbox: dict[str, float]) -> tuple[float, float]:
    """Center of the percent bbox as ``(x_pct, y_pct)`` (same convention as ``area.json``)."""
    cx = float(bbox["x"]) + float(bbox["width"]) / 2.0
    cy = float(bbox["y"]) + float(bbox["height"]) / 2.0
    return cx, cy


def bbox_percent_center_to_device_point(
    bbox: dict[str, float],
    dev_w: int,
    dev_h: int,
) -> Point:
    """Percent-bbox centre mapped to ``dev_w``×``dev_h`` framebuffer pixels."""
    cx = (bbox["x"] + bbox["width"] / 2.0) / 100.0 * dev_w
    cy = (bbox["y"] + bbox["height"] / 2.0) / 100.0 * dev_h
    return Point(int(round(cx)), int(round(cy)))


def bbox_percent_random_point_to_device_point(
    bbox: dict[str, float],
    dev_w: int,
    dev_h: int,
    *,
    inset_pct: float = 0.15,
    rng: random.Random | None = None,
) -> Point:
    """Uniformly random point inside the percent-bbox, mapped to device pixels.

    ``inset_pct`` shrinks the bbox by that fraction of its width/height on each
    side before sampling, so clicks stay away from borders (shadows, neighbour
    elements). A degenerate bbox (zero width or height) collapses to its centre.
    """
    x_pct = float(bbox["x"])
    y_pct = float(bbox["y"])
    w_pct = float(bbox["width"])
    h_pct = float(bbox["height"])

    if w_pct <= 0.0 or h_pct <= 0.0:
        return bbox_percent_center_to_device_point(bbox, dev_w, dev_h)

    inset = max(0.0, min(0.49, float(inset_pct)))
    inset_w = w_pct * inset
    inset_h = h_pct * inset

    x0 = (x_pct + inset_w) / 100.0 * dev_w
    x1 = (x_pct + w_pct - inset_w) / 100.0 * dev_w
    y0 = (y_pct + inset_h) / 100.0 * dev_h
    y1 = (y_pct + h_pct - inset_h) / 100.0 * dev_h

    r = rng or random
    cx = r.uniform(x0, x1)
    cy = r.uniform(y0, y1)
    return Point(int(round(cx)), int(round(cy)))
