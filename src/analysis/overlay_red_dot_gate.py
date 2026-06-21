"""Red-dot probes and the shared ``findIcon`` gate/finalize pipeline.

Extracted verbatim from ``analysis.overlay_engine``, which re-exports every
name here — keep importing via ``analysis.overlay_engine`` from consumers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from analysis.overlay_geometry import _relative_bbox_percent_from_top_left
from analysis.overlay_template_match import (
    _apply_bright_detail_gate,
    _apply_min_saturation_gate,
    _bright_low_saturation_ratio,
)
from layout.red_dot_detector import has_red_dot_in_bbox_percent

if TYPE_CHECKING:
    import numpy as np

def _probe_red_dot_within_zone(image_bgr: np.ndarray, bbox: dict[str, float]) -> bool:
    """Search only inside the labeled region rectangle (``has_red_dot`` bbox)."""
    return bool(
        has_red_dot_in_bbox_percent(
            image_bgr, bbox, pad_px=0, edge_badge_pad_ratio=0.0
        )
    )


def build_static_red_dot_hit(
    *,
    region: str,
    region_def: dict[str, Any],
    image_bgr: np.ndarray,
    requirement: bool,
    tap_delta: tuple[str, float, float] | None = None,
) -> dict[str, Any]:
    """Build the shared standalone red-dot probe row.

    This is the one model for ``isRedDot`` / ``action: red_dot`` when the badge
    is searched inside a labeled static bbox. Dynamic badge checks after a
    moving template match still go through ``findIcon + isRedDot``.
    """
    want_present = bool(requirement)
    hit: dict[str, Any] = {
        "matched": False,
        "action": "red_dot",
        "region": region,
        "want_dot_present": want_present,
        "red_dot_required": want_present,
    }
    if not bool(region_def.get("has_red_dot")):
        hit["reason"] = "red_dot_capability_disabled"
        return hit
    if bool(region_def.get("isSearch")):
        hit["reason"] = "red_dot_within_zone_only"
        hit["detail"] = (
            "isSearch is dynamic findIcon search; use findIcon+isRedDot "
            "for badge probes relative to the match, not standalone isRedDot"
        )
        return hit

    bbox = region_def.get("bbox") if isinstance(region_def.get("bbox"), dict) else None
    if bbox is None:
        hit["reason"] = "missing_bbox_for_red_dot"
        return hit

    present = _probe_red_dot_within_zone(image_bgr, bbox)
    matched = present if want_present else not present

    try:
        cx = float(bbox.get("x") or 0.0) + float(bbox.get("width") or 0.0) / 2.0
        cy = float(bbox.get("y") or 0.0) + float(bbox.get("height") or 0.0) / 2.0
    except (TypeError, ValueError):
        cx = cy = 0.0

    tap_x = cx
    tap_y = cy
    if tap_delta is not None:
        _tap_region, dx_pct, dy_pct = tap_delta
        tap_x = cx + dx_pct
        tap_y = cy + dy_pct

    hit.update(
        {
            "matched": matched,
            "red_dot_present": present,
            "red_dot_search_mode": "within_zone",
            "tap_x_pct": tap_x,
            "tap_y_pct": tap_y,
            "tap_match_x_pct": cx,
            "tap_match_y_pct": cy,
        }
    )
    if tap_delta is not None:
        tap_region, dx_pct, dy_pct = tap_delta
        hit["tap_region"] = tap_region
        hit["tap_delta_x_pct"] = dx_pct
        hit["tap_delta_y_pct"] = dy_pct
    if present != want_present:
        hit["reason"] = "red_dot_missing" if want_present else "red_dot_unexpected"
    return hit


def _probe_red_dot_at_template_match(
    image_bgr: np.ndarray,
    top_left: tuple[int, int],
    template_w: int,
    template_h: int,
    rule: dict[str, Any],
) -> tuple[bool, dict[str, float]]:
    """Search relative to a dynamically found template — not the static labeled bbox."""
    hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    probe_bbox = _relative_bbox_percent_from_top_left(
        top_left,
        template_w,
        template_h,
        _direct_template_red_dot_bbox(rule),
        image_w=wi,
        image_h=hi,
    )
    present = bool(
        has_red_dot_in_bbox_percent(
            image_bgr, probe_bbox, pad_px=0, edge_badge_pad_ratio=0.0
        )
    )
    return present, probe_bbox


def _apply_findicon_red_dot_gate(
    *,
    matched: bool,
    rule: dict[str, Any],
    image_bgr: np.ndarray,
    top_left: tuple[int, int],
    template_w: int,
    template_h: int,
) -> tuple[bool, bool | None, dict[str, float] | None]:
    """Optional ``isRedDot`` gate after a sliding or full-frame ``findIcon`` match."""
    red_dot_required = (
        rule.get("isRedDot") if isinstance(rule.get("isRedDot"), bool) else None
    )
    if not matched or red_dot_required is None:
        return matched, None, None
    present, probe_bbox = _probe_red_dot_at_template_match(
        image_bgr, top_left, template_w, template_h, rule
    )
    matched = present if red_dot_required else not present
    return matched, present, probe_bbox


def _direct_template_red_dot_bbox(rule: dict[str, Any]) -> dict[str, float]:
    raw = rule.get("red_dot_bbox")
    if isinstance(raw, dict):
        try:
            return {
                "x": float(raw.get("x")),
                "y": float(raw.get("y")),
                "width": float(raw.get("width")),
                "height": float(raw.get("height")),
            }
        except (TypeError, ValueError):
            pass
    # Default notification badge slot for event icons: top-right, slightly
    # above the icon bbox. Percentages are relative to the matched template.
    return {"x": 68.0, "y": -32.0, "width": 56.0, "height": 63.0}



def _finalize_findicon_hit(
    *,
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    res: dict[str, Any],
    matched: bool,
    score: Any,
    threshold: float,
    template_w: int,
    template_h: int,
    rule: dict[str, Any],
    min_sat: float | None,
    min_patch_bright_ratio: float | None,
    region_name: str,
    resolved_region_name: str,
    resolved_version: Any,
    match_x_pct: float,
    match_y_pct: float,
    tap_delta: tuple[str, float, float] | None,
    push_tasks: Any,
    set_node_s: Any,
    priority: Any,
    extra_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply the shared ``findIcon`` gates and build the result hit dict.

    Every ``findIcon`` path (direct-template, full-frame cache, search-ROI,
    primary-bbox) funnels through here so the bright-detail / saturation /
    red-dot gates and the emitted fields stay identical. Path-specific score
    fields (``score_ncc_second``, ``score_edge``, ``hash_distance``,
    ``match_source``, ``template``, ``search_region``) are passed via
    ``extra_fields``; the matcher itself stays per-path.
    """
    tl_tuple = (int(res["top_left"][0]), int(res["top_left"][1]))
    sat_fail: str | None = None
    mean_sat: float | None = None
    bright_fail: str | None = None
    tpl_bright: float | None = None
    patch_bright: float | None = None
    if matched:
        ok, tpl_bright, patch_bright, bright_fail = _apply_bright_detail_gate(
            image_bgr, template_bgr, tl_tuple
        )
        matched = ok
    if matched and min_patch_bright_ratio is not None:
        patch = image_bgr[
            tl_tuple[1] : tl_tuple[1] + int(template_h),
            tl_tuple[0] : tl_tuple[0] + int(template_w),
        ]
        if tpl_bright is None:
            tpl_bright = _bright_low_saturation_ratio(template_bgr)
        patch_bright = _bright_low_saturation_ratio(patch)
        if patch_bright < float(min_patch_bright_ratio):
            bright_fail = "low_patch_bright_ratio"
            matched = False
    if matched and min_sat is not None:
        ok, mean_sat, sat_fail = _apply_min_saturation_gate(
            image_bgr, tl_tuple, template_w, template_h, min_sat
        )
        matched = ok
    matched, red_dot_present, red_dot_bbox = _apply_findicon_red_dot_gate(
        matched=matched,
        rule=rule,
        image_bgr=image_bgr,
        top_left=tl_tuple,
        template_w=template_w,
        template_h=template_h,
    )

    tap_x_pct = match_x_pct
    tap_y_pct = match_y_pct
    if tap_delta is not None:
        _tap_region, dx_pct, dy_pct = tap_delta
        tap_x_pct = match_x_pct + dx_pct
        tap_y_pct = match_y_pct + dy_pct

    hit: dict[str, Any] = {
        "matched": matched,
        "score": score,
        "score_ncc": res.get("score_ncc"),
    }
    if extra_fields:
        hit.update(extra_fields)
    hit.update(
        {
            "threshold": threshold,
            "top_left": list(res["top_left"]),
            "template_w": template_w,
            "template_h": template_h,
            "action": "findIcon",
            "region": region_name,
            "resolved_region": resolved_region_name,
            "resolved_version": resolved_version,
            "tap_x_pct": tap_x_pct,
            "tap_y_pct": tap_y_pct,
            "tap_match_x_pct": match_x_pct,
            "tap_match_y_pct": match_y_pct,
        }
    )
    if tap_delta is not None:
        tap_region, dx_pct, dy_pct = tap_delta
        hit["tap_region"] = tap_region
        hit["tap_delta_x_pct"] = dx_pct
        hit["tap_delta_y_pct"] = dy_pct
    if push_tasks:
        hit["pushScenario"] = push_tasks
    if set_node_s:
        hit["set_node"] = set_node_s
    if priority is not None:
        hit["priority"] = priority
    if min_sat is not None:
        hit["min_match_saturation"] = min_sat
    if min_patch_bright_ratio is not None:
        hit["min_patch_bright_ratio"] = min_patch_bright_ratio
    if tpl_bright is not None:
        hit["template_bright_ratio"] = tpl_bright
        hit["patch_bright_ratio"] = patch_bright
    if mean_sat is not None:
        hit["mean_saturation"] = mean_sat
    if bright_fail or sat_fail:
        hit["reason"] = bright_fail or sat_fail
    if isinstance(rule.get("isRedDot"), bool):
        hit["red_dot_required"] = rule["isRedDot"]
        hit["red_dot_present"] = bool(red_dot_present)
        hit["red_dot_search_mode"] = "dynamic"
        if red_dot_bbox is not None:
            hit["red_dot_bbox"] = red_dot_bbox
    return hit
