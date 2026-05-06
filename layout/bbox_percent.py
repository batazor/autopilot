"""Map percent-based bbox (labeling / ``area.json``) to device pixel taps."""

from __future__ import annotations

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
