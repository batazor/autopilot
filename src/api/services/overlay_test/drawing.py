"""Overlay shape builders (match rects, search ROIs, tap markers) for the canvas."""
from __future__ import annotations

from typing import Any

from api.services.click_approval_overlay import (
    OverlayCrosshair,
    OverlayRect,
    OverlayShape,
)
from api.services.overlay_test.common import _bbox_pct_to_px, _coerce_float
from layout.area_lookup import screen_region_by_name

_STROKE_MATCHED = "#22c55e"
_STROKE_UNMATCHED = "#64748b"
_STROKE_SEARCH_ROI = "#f59e0b"
_STROKE_REGION_BBOX = "#3b82f6"


def _add_match_rect(
    overlays: list[OverlayShape],
    *,
    payload: dict[str, Any],
    rule_name: str,
    matched: bool,
    w: int,
    h: int,
) -> None:
    tl = payload.get("top_left")
    tw = int(payload.get("template_w") or 0)
    th = int(payload.get("template_h") or 0)
    if not (isinstance(tl, (list, tuple)) and len(tl) >= 2 and tw > 0 and th > 0):
        return
    try:
        x0 = int(float(tl[0]))
        y0 = int(float(tl[1]))
    except (TypeError, ValueError):
        return
    x0 = max(0, min(w - 1, x0))
    y0 = max(0, min(h - 1, y0))
    rw = max(1, min(w - x0, tw))
    rh = max(1, min(h - y0, th))
    stroke = _STROKE_MATCHED if matched else _STROKE_UNMATCHED
    label = rule_name + (" ✓" if matched else " ✗")
    overlays.append(
        OverlayRect(type="rect", x=x0, y=y0, w=rw, h=rh, label=label, stroke=stroke)
    )


def _add_search_roi(
    overlays: list[OverlayShape],
    *,
    payload: dict[str, Any],
    rule_search_name: str,
    area_doc: dict[str, Any],
    w: int,
    h: int,
) -> None:
    sr_name = str(payload.get("search_region") or rule_search_name or "").strip()
    if not sr_name:
        return
    if sr_name == "full_frame_cache":
        overlays.append(
            OverlayRect(
                type="rect",
                x=0,
                y=0,
                w=w,
                h=h,
                label="search:full frame",
                stroke=_STROKE_SEARCH_ROI,
            )
        )
        return
    pair = screen_region_by_name(area_doc, sr_name)
    if pair is None:
        return
    sr_bbox = pair[1].get("bbox")
    if not isinstance(sr_bbox, dict):
        return
    left, top, right, bottom = _bbox_pct_to_px(sr_bbox, w, h)
    overlays.append(
        OverlayRect(
            type="rect",
            x=left,
            y=top,
            w=right - left,
            h=bottom - top,
            label=f"search:{sr_name}",
            stroke=_STROKE_SEARCH_ROI,
        )
    )


def _add_region_bbox_fallback(
    overlays: list[OverlayShape],
    *,
    region_name: str,
    area_doc: dict[str, Any],
    state_flat: dict[str, Any] | None,
    w: int,
    h: int,
    rule_name: str,
    matched: bool,
) -> None:
    """When the matcher didn't expose ``top_left``, fall back to the region bbox.

    Region detectors (red_dot, color_check, ocr) don't return a template top-left
    because nothing was template-matched — they probe the whole bbox. Drawing the
    region itself keeps the visualization useful for those rules.
    """
    pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
    if pair is None:
        return
    bb = pair[1].get("bbox")
    if not isinstance(bb, dict):
        return
    left, top, right, bottom = _bbox_pct_to_px(bb, w, h)
    stroke = _STROKE_MATCHED if matched else _STROKE_REGION_BBOX
    label = rule_name + (" ✓" if matched else "")
    overlays.append(
        OverlayRect(
            type="rect",
            x=left,
            y=top,
            w=right - left,
            h=bottom - top,
            label=label,
            stroke=stroke,
        )
    )


def _add_tap_marker_if_any(
    overlays: list[OverlayShape],
    *,
    payload: dict[str, Any],
    w: int,
    h: int,
) -> None:
    tap_x_pct = payload.get("tap_x_pct")
    tap_y_pct = payload.get("tap_y_pct")
    if tap_x_pct is None or tap_y_pct is None:
        return
    try:
        x_px = int(float(tap_x_pct) / 100.0 * w)
        y_px = int(float(tap_y_pct) / 100.0 * h)
    except (TypeError, ValueError):
        return
    overlays.append(
        OverlayCrosshair(
            type="crosshair",
            x=max(0, min(w - 1, x_px)),
            y=max(0, min(h - 1, y_px)),
        )
    )


def _add_probe_search_area(
    overlays: list[OverlayShape],
    *,
    payload: dict[str, Any],
    region_name: str,
    area_doc: dict[str, Any],
    state_flat: dict[str, Any] | None,
    w: int,
    h: int,
) -> None:
    """Draw the area that the probe searched, even for fixed-bbox 1:1 checks."""
    sr_name = str(payload.get("search_region") or "").strip()
    if sr_name == "full_frame_cache":
        overlays.append(
            OverlayRect(
                type="rect",
                x=0,
                y=0,
                w=w,
                h=h,
                label="search:full frame",
                stroke=_STROKE_SEARCH_ROI,
            )
        )
        return
    search_name = sr_name or region_name
    pair = screen_region_by_name(area_doc, search_name, state_flat=state_flat)
    if pair is None:
        return
    bb = pair[1].get("bbox")
    if not isinstance(bb, dict):
        return
    left, top, right, bottom = _bbox_pct_to_px(bb, w, h)
    overlays.append(
        OverlayRect(
            type="rect",
            x=left,
            y=top,
            w=right - left,
            h=bottom - top,
            label=f"search:{search_name}",
            stroke=_STROKE_SEARCH_ROI,
        )
    )


def _add_probe_best_match(
    overlays: list[OverlayShape],
    *,
    payload: dict[str, Any],
    region_name: str,
    area_doc: dict[str, Any],
    state_flat: dict[str, Any] | None,
    w: int,
    h: int,
) -> None:
    matched = bool(payload.get("matched"))
    score = _coerce_float(payload.get("score"))
    threshold = _coerce_float(payload.get("threshold"))
    tl = payload.get("top_left")
    tw = int(payload.get("template_w") or 0)
    th = int(payload.get("template_h") or 0)
    if isinstance(tl, (list, tuple)) and len(tl) >= 2 and tw > 0 and th > 0:
        try:
            x0 = int(float(tl[0]))
            y0 = int(float(tl[1]))
        except (TypeError, ValueError):
            x0 = y0 = -1
        if x0 >= 0 and y0 >= 0:
            x0 = max(0, min(w - 1, x0))
            y0 = max(0, min(h - 1, y0))
            label_bits = [region_name, "match" if matched else "best"]
            if score is not None:
                label_bits.append(f"{score:.3f}")
            if threshold is not None:
                label_bits.append(f"/ {threshold:.3f}")
            overlays.append(
                OverlayRect(
                    type="rect",
                    x=x0,
                    y=y0,
                    w=max(1, min(w - x0, tw)),
                    h=max(1, min(h - y0, th)),
                    label=" ".join(label_bits),
                    stroke=_STROKE_MATCHED if matched else _STROKE_UNMATCHED,
                )
            )
            return

    _add_region_bbox_fallback(
        overlays,
        region_name=region_name,
        area_doc=area_doc,
        state_flat=state_flat,
        w=w,
        h=h,
        rule_name=region_name,
        matched=matched,
    )
