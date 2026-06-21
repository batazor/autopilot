"""Bbox/region coordinate conversions and tap-offset helpers for the overlay engine.

Extracted verbatim from ``analysis.overlay_engine``, which re-exports every
name here — keep importing via ``analysis.overlay_engine`` from consumers.
"""

from __future__ import annotations

from typing import Any

from analysis.overlay_rules import centers_delta_pct_between_regions
from layout.types import Region


def _bbox_percent_to_region_px(
    bbox: dict[str, float],
    wi: int,
    hi: int,
) -> Region:
    x = float(bbox.get("x") or 0.0)
    y = float(bbox.get("y") or 0.0)
    w = float(bbox.get("width") or 0.0)
    h = float(bbox.get("height") or 0.0)
    left = max(0, min(wi - 1, int(round(x / 100.0 * wi))))
    top = max(0, min(hi - 1, int(round(y / 100.0 * hi))))
    width = max(1, min(wi - left, int(round(w / 100.0 * wi))))
    height = max(1, min(hi - top, int(round(h / 100.0 * hi))))
    return Region(left, top, width, height)


def _region_to_xyxy(region: Region) -> tuple[int, int, int, int]:
    """Convert Region(x,y,w,h) to (x1,y1,x2,y2) for numpy slicing."""
    x1 = int(region.x)
    y1 = int(region.y)
    x2 = int(region.x + region.w)
    y2 = int(region.y + region.h)
    return x1, y1, x2, y2


def _relative_bbox_percent_from_top_left(
    top_left: tuple[int, int],
    width_px: int,
    height_px: int,
    rel_bbox: dict[str, float],
    *,
    image_w: int,
    image_h: int,
) -> dict[str, float]:
    x, y = top_left
    left = x + float(rel_bbox.get("x") or 0.0) / 100.0 * float(width_px)
    top = y + float(rel_bbox.get("y") or 0.0) / 100.0 * float(height_px)
    width = float(rel_bbox.get("width") or 0.0) / 100.0 * float(width_px)
    height = float(rel_bbox.get("height") or 0.0) / 100.0 * float(height_px)
    return {
        "x": 100.0 * left / float(image_w),
        "y": 100.0 * top / float(image_h),
        "width": 100.0 * width / float(image_w),
        "height": 100.0 * height / float(image_h),
        "rotation": 0.0,
        "original_width": image_w,
        "original_height": image_h,
    }



def _tap_region_delta_pct(
    area_doc: dict[str, Any],
    region_name: str,
    rule: dict[str, Any],
    *,
    state_flat: dict[str, Any] | None = None,
    screen_id: str | None = None,
) -> tuple[str, float, float] | None:
    tap_region = str(rule.get("tap_region") or f"{region_name}_tap").strip()
    if not tap_region:
        return None
    delta = centers_delta_pct_between_regions(
        area_doc,
        region_name,
        tap_region,
        state_flat=state_flat,
        screen_id=screen_id,
    )
    if delta is None:
        return None
    return tap_region, delta[0], delta[1]

