from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from analysis.overlay_rules import (
    centers_delta_pct_between_regions,
    optional_expected_texts,
    optional_min_match_saturation,
    optional_prefer_primary_bbox,
    optional_priority,
    optional_push_scenario_tasks,
    optional_ttl_seconds,
    overlay_rule_screen_allowlist,
    resolved_search_region_for_findicon,
)
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region, region_version_of
from layout.color_bucket import dominant_color_label_bgr
from layout.crop_paths import exported_crop_png
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from layout.tab_active_detector import (
    TAB_ACTIVE_MAX_MEAN_SATURATION,
    TAB_ACTIVE_MIN_MEAN_VALUE,
    TAB_ACTIVE_MIN_YELLOW_RATIO,
    tab_activity_stats,
    yellow_tab_ratio,
)
from layout.template_match import (
    TemplateMatchResult,
    match_crop_1to1_at_bbox_percent,
    match_patch_bgr_at_top_left,
    match_template_full_frame_cached,
    match_template_in_search_roi_bbox_percent,
    patch_bgr_from_bbox_percent,
    patch_mean_hsv_saturation,
    template_cache_key,
    validate_live_bbox_patch_vs_reference_dims,
)
from layout.types import Region
from layout.white_border_detector import (
    WHITE_BORDER_HALO_PX,
    WHITE_BORDER_MAX_MEAN_SATURATION,
    WHITE_BORDER_MIN_INTERIOR_SATURATION_EXCESS,
    WHITE_BORDER_MIN_MEAN_VALUE,
    WHITE_BORDER_MIN_RING_PIXELS,
    has_white_border_in_bbox_percent,
    white_border_halo_stats,
)
from ocr.client import OcrClient
from ocr.fuzzy import match as fuzzy_match
from ocr.preprocess import resolve_preprocess

# Cap entry count; PNG crops are small (typically <10 KB each), so the upper
# bound on memory is well under 10 MB even when full. Sized to comfortably
# cover the active overlay rule fleet (~hundreds of distinct crops).
_TEMPLATE_CACHE_MAX = 512
_template_cache: dict[tuple[str, int], np.ndarray] = {}
_template_mask_cache: dict[tuple[str, int], tuple[np.ndarray, np.ndarray | None]] = {}


def _load_template_cached(path: Path) -> np.ndarray | None:
    """Decode a PNG template once, then reuse — invalidates on mtime change.

    Returned arrays are shared; callers must treat them as read-only (template
    match routines only sample, never mutate).
    """
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(path), mtime_ns)
    tpl = _template_cache.get(key)
    if tpl is not None:
        return tpl
    tpl = cv2.imread(str(path))
    if tpl is None:
        return None
    if len(_template_cache) >= _TEMPLATE_CACHE_MAX:
        for old_key in list(_template_cache.keys())[: _TEMPLATE_CACHE_MAX // 4]:
            _template_cache.pop(old_key, None)
    _template_cache[key] = tpl
    return tpl


def _load_template_with_mask_cached(path: Path) -> tuple[np.ndarray, np.ndarray | None] | None:
    """Decode a direct PNG template, preserving alpha as an optional match mask."""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(path), mtime_ns)
    cached = _template_mask_cache.get(key)
    if cached is not None:
        return cached
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        return None
    if raw.ndim == 3 and raw.shape[2] == 4:
        alpha = raw[:, :, 3]
        bgr = raw[:, :, :3]
        mask = (alpha > 8).astype(np.uint8) * 255
        ys, xs = np.where(mask > 0)
        if len(xs) and len(ys):
            bgr = bgr[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]
            mask = mask[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]
        if mask.size and bool(np.all(mask == 255)):
            mask = None
        out = (bgr, mask)
    else:
        out = (raw, None)
    if len(_template_mask_cache) >= _TEMPLATE_CACHE_MAX:
        for old_key in list(_template_mask_cache.keys())[: _TEMPLATE_CACHE_MAX // 4]:
            _template_mask_cache.pop(old_key, None)
    _template_mask_cache[key] = out
    return out


def _match_direct_template_in_bbox(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    template_mask: np.ndarray | None,
    search_bbox: dict[str, float],
) -> TemplateMatchResult:
    search, (left, top) = patch_bgr_from_bbox_percent(image_bgr, search_bbox)
    tw = int(template_bgr.shape[1])
    th = int(template_bgr.shape[0])
    if tw <= search.shape[1] and th <= search.shape[0]:
        heat = cv2.matchTemplate(search, template_bgr, cv2.TM_CCORR_NORMED, mask=template_mask)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(heat)
        if not np.isfinite(max_val):
            max_val = 0.0
        return TemplateMatchResult(
            score=float(max_val),
            top_left=(int(left + max_loc[0]), int(top + max_loc[1])),
            score_ncc=float(max_val),
            score_ncc_second=None,
            match_source="direct_template",
            hash_distance=None,
            template_w=tw,
            template_h=th,
        )
    return TemplateMatchResult(
        score=0.0,
        top_left=(int(left), int(top)),
        score_ncc=0.0,
        score_ncc_second=None,
        match_source="direct_template",
        hash_distance=None,
        template_w=tw,
        template_h=th,
    )


def _apply_min_saturation_gate(
    image_bgr: np.ndarray,
    top_left: tuple[int, int],
    tw: int,
    th: int,
    min_s: float,
) -> tuple[bool, float | None, str | None]:
    """Returns ``(passes, mean_saturation_or_none, fail_reason_or_none)``."""
    patch = match_patch_bgr_at_top_left(image_bgr, top_left, tw, th)
    if patch is None:
        return False, None, "match_patch_out_of_bounds"
    mean_s = patch_mean_hsv_saturation(patch)
    if mean_s < float(min_s):
        return False, mean_s, "low_saturation"
    return True, mean_s, None


def _bright_low_saturation_ratio(patch_bgr: np.ndarray) -> float:
    """Share of bright low-saturation pixels (white/cream UI details)."""
    if patch_bgr.ndim != 3 or patch_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 1] <= 45) & (hsv[:, :, 2] >= 150)
    return float(np.mean(mask))


def _apply_bright_detail_gate(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    top_left: tuple[int, int],
) -> tuple[bool, float, float, str | None]:
    """Reject matches that lose distinctive bright low-saturation template details.

    This is intentionally automatic rather than YAML-driven: when a template contains a large
    white/cream component (for example a sleeve, text, or border), a candidate patch with almost
    none of that component is usually a geometric false positive.
    """
    template_ratio = _bright_low_saturation_ratio(template_bgr)
    # Below this share of bright low-S pixels in the reference crop, skip the gate —
    # only clearly white-heavy templates (icons with lots of cream/UI chrome) need it.
    _BRIGHT_DETAIL_TEMPLATE_MIN = 0.35
    if template_ratio < _BRIGHT_DETAIL_TEMPLATE_MIN:
        return True, template_ratio, 0.0, None
    patch = match_patch_bgr_at_top_left(
        image_bgr,
        top_left,
        int(template_bgr.shape[1]),
        int(template_bgr.shape[0]),
    )
    if patch is None:
        return False, template_ratio, 0.0, "match_patch_out_of_bounds"
    patch_ratio = _bright_low_saturation_ratio(patch)
    min_ratio = max(0.12, template_ratio * 0.35)
    if patch_ratio < min_ratio:
        return False, template_ratio, patch_ratio, "low_bright_detail_ratio"
    return True, template_ratio, patch_ratio, None


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


async def evaluate_overlay_rules_async(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo_root: Path,
    overlay_rules: list[dict[str, Any]],
    *,
    current_screen: str | None = None,
    rule_eval_state: dict[str, float] | None = None,
    state_flat: dict[str, Any] | None = None,
    ocr_client: OcrClient | None = None,
) -> dict[str, Any]:
    """Run ordered overlay rules; returns a dict keyed by rule ``name``."""
    out: dict[str, Any] = {}
    now_mono = time.monotonic()
    cur_screen_norm = (current_screen or "").strip()

    def _lookup_region(region_name: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
        return screen_region_by_name(
            area_doc,
            region_name,
            state_flat=state_flat,
        )

    # ``action: text`` rules defer their OCR to a batched pass below — one
    # ``ocr_regions`` call covers every primary bbox in this tick, plus a
    # a single OCR batch for primary regions
    # against the primary OCR text. Replaces 130+ sequential HTTP calls on
    # ``screen_verify.yaml`` with at most 2 round-trips.
    pending_text_rules: list[dict[str, Any]] = []
    for rule in overlay_rules:
        if not isinstance(rule, dict):
            continue
        set_node = rule.get("set_node")
        set_node_s = str(set_node).strip() if isinstance(set_node, str) else ""
        priority = optional_priority(rule)
        # Screen filter: skip screen-specific rules when current screen doesn't match.
        # Compare case-insensitively (Redis / FSM may differ in casing from YAML).
        allowlist = overlay_rule_screen_allowlist(rule)
        if allowlist:
            allowed_lc = {s.lower() for s in allowlist}
            wants_unknown = "none" in allowed_lc
            cur_lc = cur_screen_norm.lower()
            # Glob support: a pattern that includes ``*`` or ``?`` matches
            # any screen that fnmatch'es it — used by ``page.heroes.*``
            # rules so the per-hero screens (62 of them) don't need to be
            # spelled out one by one.
            glob_patterns = [p for p in allowed_lc if "*" in p or "?" in p]
            if cur_screen_norm:
                matched = cur_lc in allowed_lc
                if not matched and glob_patterns:
                    import fnmatch
                    matched = any(
                        fnmatch.fnmatchcase(cur_lc, pat) for pat in glob_patterns
                    )
                if not matched:
                    continue
            else:
                if not wants_unknown:
                    continue
        action = str(rule.get("action") or "").strip()
        logical_name = str(rule.get("name") or "").strip()
        if not logical_name:
            continue

        ttl_seconds = optional_ttl_seconds(rule)
        if ttl_seconds is not None and rule_eval_state is not None:
            last = rule_eval_state.get(logical_name)
            if last is not None and (now_mono - last) < ttl_seconds:
                out[logical_name] = {
                    "matched": False,
                    "reason": "ttl_throttled",
                    "ttl": ttl_seconds,
                    "next_eval_in": max(0.0, ttl_seconds - (now_mono - last)),
                    "region": str(rule.get("region") or "").strip(),
                }
                continue
            rule_eval_state[logical_name] = now_mono

        # `area.json` (labeling editor) uses action names:
        # - exist -> template match (`findIcon`)
        # - text  -> OCR (`text`)
        if action == "exist":
            action = "findIcon"
        # Only `text` is supported for OCR.

        # YAML may use ``isRedDot: true|false`` instead of ``action:`` (same keys as DSL).
        # When an explicit findIcon/template action is present, keep findIcon and
        # use isRedDot as an additional gate on the found icon.
        if rule.get("isRedDot") is True and action != "findIcon":
            action = "red_dot"
        elif rule.get("isRedDot") is False and action != "findIcon":
            action = "red_dot_absent"

        # YAML may use ``isTabActive: true|false`` to gate on tab highlight state.
        if rule.get("isTabActive") is True:
            action = "tab_active"
        elif rule.get("isTabActive") is False:
            action = "tab_active_absent"

        # YAML may use ``isWhiteBorder: true|false`` to gate on a near-white halo.
        if rule.get("isWhiteBorder") is True:
            action = "white_border"
        elif rule.get("isWhiteBorder") is False:
            action = "white_border_absent"

        if action in ("red_dot", "red_dot_absent"):
            want_present = action == "red_dot"
            region_name_rd = str(rule.get("region") or "").strip()
            pair_rd = _lookup_region(region_name_rd) if region_name_rd else None
            if pair_rd is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name_rd,
                    "action": "red_dot",
                    "want_dot_present": want_present,
                }
                continue
            _entry_rd, reg_rd = pair_rd
            if not bool(reg_rd.get("has_red_dot")):
                out[logical_name] = {
                    "matched": False,
                    "reason": "red_dot_capability_disabled",
                    "region": region_name_rd,
                    "action": "red_dot",
                    "want_dot_present": want_present,
                }
                continue
            bbox_rd = reg_rd.get("bbox")
            if not isinstance(bbox_rd, dict):
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox",
                    "region": region_name_rd,
                    "action": "red_dot",
                    "want_dot_present": want_present,
                }
                continue
            present_rd = bool(has_red_dot_in_bbox_percent(image_bgr, bbox_rd))
            matched_rd = present_rd if want_present else not present_rd

            bx = float(bbox_rd.get("x") or 0.0)
            by = float(bbox_rd.get("y") or 0.0)
            bw = float(bbox_rd.get("width") or 0.0)
            bh = float(bbox_rd.get("height") or 0.0)
            mx_pct_rd = bx + bw / 2.0
            my_pct_rd = by + bh / 2.0
            tap_x_pct_rd = mx_pct_rd
            tap_y_pct_rd = my_pct_rd
            tap_delta_rd = _tap_region_delta_pct(
                area_doc,
                region_name_rd,
                rule,
                state_flat=state_flat,
                screen_id=cur_screen_norm or None,
            )
            if tap_delta_rd is not None:
                _tap_reg_rd, dx_pct_rd, dy_pct_rd = tap_delta_rd
                tap_x_pct_rd = mx_pct_rd + dx_pct_rd
                tap_y_pct_rd = my_pct_rd + dy_pct_rd

            hit_rd: dict[str, Any] = {
                "matched": matched_rd,
                "action": "red_dot",
                "region": region_name_rd,
                "want_dot_present": want_present,
                "red_dot_present": present_rd,
                "tap_x_pct": tap_x_pct_rd,
                "tap_y_pct": tap_y_pct_rd,
                "tap_match_x_pct": mx_pct_rd,
                "tap_match_y_pct": my_pct_rd,
            }
            if tap_delta_rd is not None:
                tap_reg_rd, dx_pct_rd, dy_pct_rd = tap_delta_rd
                hit_rd["tap_region"] = tap_reg_rd
                hit_rd["tap_delta_x_pct"] = dx_pct_rd
                hit_rd["tap_delta_y_pct"] = dy_pct_rd
            push_tasks_rd = optional_push_scenario_tasks(rule)
            if push_tasks_rd:
                hit_rd["pushScenario"] = push_tasks_rd
            if set_node_s:
                hit_rd["set_node"] = set_node_s
            if priority is not None:
                hit_rd["priority"] = priority
            out[logical_name] = hit_rd
            continue

        if action in ("tab_active", "tab_active_absent"):
            want_active = action == "tab_active"
            region_name_ta = str(rule.get("region") or "").strip()
            pair_ta = _lookup_region(region_name_ta) if region_name_ta else None
            if pair_ta is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name_ta,
                    "action": "tab_active",
                    "want_tab_active": want_active,
                }
                continue
            _entry_ta, reg_ta = pair_ta
            bbox_ta = reg_ta.get("bbox")
            if not isinstance(bbox_ta, dict):
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox",
                    "region": region_name_ta,
                    "action": "tab_active",
                    "want_tab_active": want_active,
                }
                continue
            max_s = float(
                rule.get("max_mean_saturation", TAB_ACTIVE_MAX_MEAN_SATURATION)
            )
            min_v = float(
                rule.get("min_mean_value", TAB_ACTIVE_MIN_MEAN_VALUE)
            )
            min_yellow_ratio = float(
                rule.get("min_yellow_ratio", TAB_ACTIVE_MIN_YELLOW_RATIO)
            )
            hi_ta, wi_ta = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            region_px_ta = _bbox_percent_to_region_px(bbox_ta, wi_ta, hi_ta)
            x1, y1, x2, y2 = _region_to_xyxy(region_px_ta)
            patch_ta = image_bgr[y1:y2, x1:x2]
            mean_s_ta, mean_v_ta = tab_activity_stats(patch_ta)
            yellow_ratio_ta = yellow_tab_ratio(patch_ta)
            active_ta = (
                mean_s_ta < max_s and mean_v_ta > min_v
            ) or yellow_ratio_ta >= min_yellow_ratio
            matched_ta = active_ta if want_active else not active_ta

            bx = float(bbox_ta.get("x") or 0.0)
            by = float(bbox_ta.get("y") or 0.0)
            bw = float(bbox_ta.get("width") or 0.0)
            bh = float(bbox_ta.get("height") or 0.0)
            mx_pct_ta = bx + bw / 2.0
            my_pct_ta = by + bh / 2.0
            tap_x_pct_ta = mx_pct_ta
            tap_y_pct_ta = my_pct_ta
            tap_delta_ta = _tap_region_delta_pct(
                area_doc,
                region_name_ta,
                rule,
                state_flat=state_flat,
                screen_id=cur_screen_norm or None,
            )
            if tap_delta_ta is not None:
                _tap_reg_ta, dx_pct_ta, dy_pct_ta = tap_delta_ta
                tap_x_pct_ta = mx_pct_ta + dx_pct_ta
                tap_y_pct_ta = my_pct_ta + dy_pct_ta

            hit_ta: dict[str, Any] = {
                "matched": matched_ta,
                "action": "tab_active",
                "region": region_name_ta,
                "want_tab_active": want_active,
                "tab_active": active_ta,
                "mean_saturation": mean_s_ta,
                "mean_value": mean_v_ta,
                "yellow_ratio": yellow_ratio_ta,
                "max_mean_saturation": max_s,
                "min_mean_value": min_v,
                "min_yellow_ratio": min_yellow_ratio,
                "tap_x_pct": tap_x_pct_ta,
                "tap_y_pct": tap_y_pct_ta,
                "tap_match_x_pct": mx_pct_ta,
                "tap_match_y_pct": my_pct_ta,
            }
            if tap_delta_ta is not None:
                tap_reg_ta, dx_pct_ta, dy_pct_ta = tap_delta_ta
                hit_ta["tap_region"] = tap_reg_ta
                hit_ta["tap_delta_x_pct"] = dx_pct_ta
                hit_ta["tap_delta_y_pct"] = dy_pct_ta
            push_tasks_ta = optional_push_scenario_tasks(rule)
            if push_tasks_ta:
                hit_ta["pushScenario"] = push_tasks_ta
            if set_node_s:
                hit_ta["set_node"] = set_node_s
            if priority is not None:
                hit_ta["priority"] = priority
            out[logical_name] = hit_ta
            continue

        if action in ("white_border", "white_border_absent"):
            want_border = action == "white_border"
            region_name_wb = str(rule.get("region") or "").strip()
            pair_wb = _lookup_region(region_name_wb) if region_name_wb else None
            if pair_wb is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name_wb,
                    "action": "white_border",
                    "want_white_border": want_border,
                }
                continue
            _entry_wb, reg_wb = pair_wb
            bbox_wb = reg_wb.get("bbox")
            if not isinstance(bbox_wb, dict):
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox",
                    "region": region_name_wb,
                    "action": "white_border",
                    "want_white_border": want_border,
                }
                continue
            halo_px = int(rule.get("halo_px", WHITE_BORDER_HALO_PX))
            max_s_wb = float(
                rule.get("max_mean_saturation", WHITE_BORDER_MAX_MEAN_SATURATION)
            )
            min_v_wb = float(
                rule.get("min_mean_value", WHITE_BORDER_MIN_MEAN_VALUE)
            )
            min_excess_wb = float(
                rule.get(
                    "min_interior_saturation_excess",
                    WHITE_BORDER_MIN_INTERIOR_SATURATION_EXCESS,
                )
            )
            min_ring_wb = int(
                rule.get("min_ring_pixels", WHITE_BORDER_MIN_RING_PIXELS)
            )
            halo_s_wb, halo_v_wb, inner_s_wb, ring_count_wb = white_border_halo_stats(
                image_bgr, bbox_wb, halo_px=halo_px
            )
            present_wb = bool(
                has_white_border_in_bbox_percent(
                    image_bgr,
                    bbox_wb,
                    halo_px=halo_px,
                    max_mean_saturation=max_s_wb,
                    min_mean_value=min_v_wb,
                    min_interior_saturation_excess=min_excess_wb,
                    min_ring_pixels=min_ring_wb,
                )
            )
            matched_wb = present_wb if want_border else not present_wb

            bx = float(bbox_wb.get("x") or 0.0)
            by = float(bbox_wb.get("y") or 0.0)
            bw = float(bbox_wb.get("width") or 0.0)
            bh = float(bbox_wb.get("height") or 0.0)
            mx_pct_wb = bx + bw / 2.0
            my_pct_wb = by + bh / 2.0
            tap_x_pct_wb = mx_pct_wb
            tap_y_pct_wb = my_pct_wb
            tap_delta_wb = _tap_region_delta_pct(
                area_doc,
                region_name_wb,
                rule,
                state_flat=state_flat,
                screen_id=cur_screen_norm or None,
            )
            if tap_delta_wb is not None:
                _tap_reg_wb, dx_pct_wb, dy_pct_wb = tap_delta_wb
                tap_x_pct_wb = mx_pct_wb + dx_pct_wb
                tap_y_pct_wb = my_pct_wb + dy_pct_wb

            hit_wb: dict[str, Any] = {
                "matched": matched_wb,
                "action": "white_border",
                "region": region_name_wb,
                "want_white_border": want_border,
                "white_border_present": present_wb,
                "halo_saturation": halo_s_wb,
                "halo_value": halo_v_wb,
                "interior_saturation": inner_s_wb,
                "interior_saturation_excess": inner_s_wb - halo_s_wb,
                "ring_count": int(ring_count_wb),
                "max_mean_saturation": max_s_wb,
                "min_mean_value": min_v_wb,
                "min_interior_saturation_excess": min_excess_wb,
                "min_ring_pixels": min_ring_wb,
                "halo_px": halo_px,
                "tap_x_pct": tap_x_pct_wb,
                "tap_y_pct": tap_y_pct_wb,
                "tap_match_x_pct": mx_pct_wb,
                "tap_match_y_pct": my_pct_wb,
            }
            if tap_delta_wb is not None:
                tap_reg_wb, dx_pct_wb, dy_pct_wb = tap_delta_wb
                hit_wb["tap_region"] = tap_reg_wb
                hit_wb["tap_delta_x_pct"] = dx_pct_wb
                hit_wb["tap_delta_y_pct"] = dy_pct_wb
            push_tasks_wb = optional_push_scenario_tasks(rule)
            if push_tasks_wb:
                hit_wb["pushScenario"] = push_tasks_wb
            if set_node_s:
                hit_wb["set_node"] = set_node_s
            if priority is not None:
                hit_wb["priority"] = priority
            out[logical_name] = hit_wb
            continue

        if action == "findIcon":
            region_name = str(rule.get("region") or "").strip()
            threshold = float(rule.get("threshold", 0.7))
            pair = _lookup_region(region_name)
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                }
                continue
            entry, reg = pair
            bbox = reg.get("bbox")
            resolved_region_name = str(reg.get("name") or "").strip() or region_name
            ref_rel = effective_ocr_for_region(entry, reg)
            if not isinstance(bbox, dict) or not ref_rel:
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox_or_ocr",
                }
                continue

            min_sat = optional_min_match_saturation(rule)
            push_tasks = optional_push_scenario_tasks(rule)

            direct_template = str(rule.get("template") or "").replace("\\", "/").strip()
            if direct_template:
                template_path = (repo_root / direct_template.lstrip("/")).resolve()
                try:
                    template_path.relative_to(repo_root.resolve())
                except ValueError:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "template_outside_repo",
                        "template": direct_template,
                    }
                    continue
                tpl_pair = _load_template_with_mask_cached(template_path)
                if tpl_pair is None:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "template_load_failed",
                        "template": direct_template,
                    }
                    continue
                search_bbox = bbox
                search_region_name = str(rule.get("search_region") or "").strip() or region_name
                if search_region_name != region_name:
                    pair_s = _lookup_region(search_region_name)
                    if pair_s is None:
                        out[logical_name] = {
                            "matched": False,
                            "reason": "unknown_search_region",
                            "search_region": search_region_name,
                            "region": region_name,
                        }
                        continue
                    search_bbox_raw = pair_s[1].get("bbox")
                    if not isinstance(search_bbox_raw, dict):
                        out[logical_name] = {
                            "matched": False,
                            "reason": "missing_search_bbox",
                            "search_region": search_region_name,
                        }
                        continue
                    search_bbox = search_bbox_raw
                tpl_bgr, tpl_mask = tpl_pair
                res = _match_direct_template_in_bbox(
                    image_bgr,
                    tpl_bgr,
                    tpl_mask,
                    search_bbox,
                )
                tw_tpl = int(res.get("template_w") or tpl_bgr.shape[1])
                th_tpl = int(res.get("template_h") or tpl_bgr.shape[0])
                cx_px = int(res["top_left"][0]) + tw_tpl / 2.0
                cy_px = int(res["top_left"][1]) + th_tpl / 2.0
                mx_pct = 100.0 * cx_px / int(image_bgr.shape[1])
                my_pct = 100.0 * cy_px / int(image_bgr.shape[0])
                score = float(res["score"])
                matched = score >= threshold
                red_dot_required = rule.get("isRedDot") if isinstance(rule.get("isRedDot"), bool) else None
                red_dot_present: bool | None = None
                red_dot_bbox: dict[str, float] | None = None
                if matched and red_dot_required is not None:
                    icon_bbox = _relative_bbox_percent_from_top_left(
                        (int(res["top_left"][0]), int(res["top_left"][1])),
                        tw_tpl,
                        th_tpl,
                        _direct_template_red_dot_bbox(rule),
                        image_w=int(image_bgr.shape[1]),
                        image_h=int(image_bgr.shape[0]),
                    )
                    red_dot_bbox = icon_bbox
                    red_dot_present = bool(
                        has_red_dot_in_bbox_percent(
                            image_bgr,
                            icon_bbox,
                            pad_px=0,
                            edge_badge_pad_ratio=0.0,
                        )
                    )
                    matched = red_dot_present if red_dot_required else not red_dot_present
                hit: dict[str, Any] = {
                    "matched": matched,
                    "score": score,
                    "score_ncc": res.get("score_ncc"),
                    "score_ncc_second": res.get("score_ncc_second"),
                    "threshold": threshold,
                    "top_left": list(res["top_left"]),
                    "template_w": tw_tpl,
                    "template_h": th_tpl,
                    "action": "findIcon",
                    "region": region_name,
                    "resolved_region": resolved_region_name,
                    "resolved_version": region_version_of(entry, reg),
                    "search_region": search_region_name,
                    "match_source": res.get("match_source"),
                    "tap_x_pct": mx_pct,
                    "tap_y_pct": my_pct,
                    "tap_match_x_pct": mx_pct,
                    "tap_match_y_pct": my_pct,
                    "template": direct_template,
                }
                if red_dot_required is not None:
                    hit["red_dot_required"] = red_dot_required
                    hit["red_dot_present"] = bool(red_dot_present)
                    if red_dot_bbox is not None:
                        hit["red_dot_bbox"] = red_dot_bbox
                if push_tasks:
                    hit["pushScenario"] = push_tasks
                if set_node_s:
                    hit["set_node"] = set_node_s
                if priority is not None:
                    hit["priority"] = priority
                out[logical_name] = hit
                continue

            crop_path = exported_crop_png(repo_root, ref_rel, resolved_region_name)
            if not crop_path.is_file():
                # Auto-export crop from the reference screenshot on demand.
                # Use ``patch_bgr_from_bbox_percent`` — the same floor/ceil rounding
                # the runtime match path uses — so the auto-exported template is
                # pixel-identical to the live bbox patch. Mixing ``round()`` here
                # (via ``_bbox_percent_to_region_px``) with floor/ceil in the live
                # path drifts by 1px on fractional bboxes and breaks the strict
                # ``match_crop_1to1_at_bbox_percent`` shape check.
                try:
                    ref_path = repo_root / ref_rel
                    if ref_path.is_file():
                        ref_img = cv2.imread(str(ref_path))
                        if ref_img is not None:
                            crop, _ = patch_bgr_from_bbox_percent(ref_img, bbox)
                            if crop.size > 0:
                                crop_path.parent.mkdir(parents=True, exist_ok=True)
                                cv2.imwrite(str(crop_path), crop)
                except Exception:
                    pass

            if not crop_path.is_file():
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_crop_png",
                    "path": str(crop_path.relative_to(repo_root)),
                }
                continue

            tpl = _load_template_cached(crop_path)
            if tpl is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "crop_load_failed",
                }
                continue

            min_sat = optional_min_match_saturation(rule)
            push_tasks = optional_push_scenario_tasks(rule)

            hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            tw_tpl = int(tpl.shape[1])
            th_tpl = int(tpl.shape[0])
            is_search = bool(reg.get("isSearch"))
            search_region_name = resolved_search_region_for_findicon(
                area_doc,
                region_name,
                ref_rel,
                rule,
                state_flat=state_flat,
                screen_id=cur_screen_norm or None,
            )

            try:
                if is_search:
                    excl = rule.get("exclude_top_lefts")
                    excl_pts: list[tuple[int, int]] = []
                    if isinstance(excl, list):
                        for it in excl:
                            if isinstance(it, (list, tuple)) and len(it) >= 2:
                                try:
                                    excl_pts.append((int(float(it[0])), int(float(it[1]))))
                                except (TypeError, ValueError):
                                    continue
                    try:
                        excl_r = int(rule.get("exclude_radius_px") or 0)
                    except (TypeError, ValueError):
                        excl_r = 0
                    res = match_template_full_frame_cached(
                        image_bgr,
                        tpl,
                        cache_key=template_cache_key(
                            region_name=resolved_region_name,
                            reference_rel=ref_rel,
                            template_bgr=tpl,
                            screen_shape=(hi, wi),
                        ),
                        threshold=threshold,
                        exclude_top_lefts=excl_pts or None,
                        exclude_radius_px=excl_r,
                    )
                    cx_px = res["top_left"][0] + tw_tpl / 2.0
                    cy_px = res["top_left"][1] + th_tpl / 2.0
                    mx_pct = 100.0 * cx_px / wi
                    my_pct = 100.0 * cy_px / hi
                    tap_x_pct = mx_pct
                    tap_y_pct = my_pct
                    tap_delta = _tap_region_delta_pct(
                        area_doc,
                        region_name,
                        rule,
                        state_flat=state_flat,
                        screen_id=cur_screen_norm or None,
                    )
                    if tap_delta is not None:
                        _tap_region, dx_pct, dy_pct = tap_delta
                        tap_x_pct = mx_pct + dx_pct
                        tap_y_pct = my_pct + dy_pct
                    score = res["score"]
                    matched = score >= threshold
                    tl_tuple = (int(res["top_left"][0]), int(res["top_left"][1]))
                    sat_fail: str | None = None
                    mean_sat: float | None = None
                    bright_fail: str | None = None
                    tpl_bright: float | None = None
                    patch_bright: float | None = None
                    if matched:
                        ok, tpl_bright, patch_bright, bright_fail = _apply_bright_detail_gate(
                            image_bgr, tpl, tl_tuple
                        )
                        matched = ok
                    if matched and min_sat is not None:
                        ok, mean_sat, sat_fail = _apply_min_saturation_gate(
                            image_bgr, tl_tuple, tw_tpl, th_tpl, min_sat
                        )
                        matched = ok
                    hit: dict[str, Any] = {
                        "matched": matched,
                        "score": score,
                        "score_ncc": res.get("score_ncc"),
                        "score_ncc_second": res.get("score_ncc_second"),
                        "score_color": res.get("score_color"),
                        "score_edge": res.get("score_edge"),
                        "threshold": threshold,
                        "top_left": list(res["top_left"]),
                        "template_w": tw_tpl,
                        "template_h": th_tpl,
                        "action": "findIcon",
                        "region": region_name,
                        "resolved_region": resolved_region_name,
                        "resolved_version": region_version_of(entry, reg),
                        "search_region": "full_frame_cache",
                        "match_source": res.get("match_source"),
                        "hash_distance": res.get("hash_distance"),
                        "tap_x_pct": tap_x_pct,
                        "tap_y_pct": tap_y_pct,
                    }
                    hit["tap_match_x_pct"] = mx_pct
                    hit["tap_match_y_pct"] = my_pct
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
                    if tpl_bright is not None:
                        hit["template_bright_ratio"] = tpl_bright
                        hit["patch_bright_ratio"] = patch_bright
                    if mean_sat is not None:
                        hit["mean_saturation"] = mean_sat
                    if bright_fail or sat_fail:
                        hit["reason"] = bright_fail or sat_fail
                    out[logical_name] = hit
                    continue

                if search_region_name:
                    pair_s = _lookup_region(search_region_name)
                    if pair_s is None:
                        out[logical_name] = {
                            "matched": False,
                            "reason": "unknown_search_region",
                            "search_region": search_region_name,
                            "region": region_name,
                        }
                        continue
                    entry_s, reg_s = pair_s
                    ref_search = str(entry_s.get("ocr") or "").strip()
                    same_screen = (
                        str(entry_s.get("screen_id") or "").strip()
                        and str(entry_s.get("screen_id") or "").strip()
                        == str(entry.get("screen_id") or "").strip()
                    )
                    if ref_search != ref_rel and not same_screen:
                        out[logical_name] = {
                            "matched": False,
                            "reason": "search_region_screen_mismatch",
                            "region": region_name,
                            "search_region": search_region_name,
                            "detail": "search_region must use the same ocr frame or screen_id as region",
                        }
                        continue
                    search_bbox = reg_s.get("bbox")
                    if not isinstance(search_bbox, dict):
                        out[logical_name] = {
                            "matched": False,
                            "reason": "missing_search_bbox",
                            "search_region": search_region_name,
                        }
                        continue
                    excl = rule.get("exclude_top_lefts")
                    excl_pts: list[tuple[int, int]] = []
                    if isinstance(excl, list):
                        for it in excl:
                            if isinstance(it, (list, tuple)) and len(it) >= 2:
                                try:
                                    excl_pts.append((int(float(it[0])), int(float(it[1]))))
                                except (TypeError, ValueError):
                                    continue
                    try:
                        excl_r = int(rule.get("exclude_radius_px") or 0)
                    except (TypeError, ValueError):
                        excl_r = 0

                    res = None
                    if optional_prefer_primary_bbox(rule):
                        try:
                            cand = match_crop_1to1_at_bbox_percent(image_bgr, tpl, bbox)
                        except ValueError:
                            cand = None
                        if cand is not None and cand["score"] >= threshold:
                            tl_cand = (int(cand["top_left"][0]), int(cand["top_left"][1]))
                            ok_b, _tb, _pb, _bf = _apply_bright_detail_gate(
                                image_bgr, tpl, tl_cand
                            )
                            ok_s = True
                            if ok_b and min_sat is not None:
                                ok_s, _ms, _sf = _apply_min_saturation_gate(
                                    image_bgr, tl_cand, tw_tpl, th_tpl, min_sat
                                )
                            if ok_b and ok_s:
                                res = cand

                    if res is None:
                        res = match_template_in_search_roi_bbox_percent(
                            image_bgr,
                            tpl,
                            search_bbox,
                            exclude_top_lefts=excl_pts or None,
                            exclude_radius_px=excl_r,
                            primary_bbox_percent=bbox,
                        )
                    cx_px = res["top_left"][0] + tw_tpl / 2.0
                    cy_px = res["top_left"][1] + th_tpl / 2.0
                    mx_pct = 100.0 * cx_px / wi
                    my_pct = 100.0 * cy_px / hi
                    tap_x_pct = mx_pct
                    tap_y_pct = my_pct
                    tap_delta = _tap_region_delta_pct(
                        area_doc,
                        region_name,
                        rule,
                        state_flat=state_flat,
                        screen_id=cur_screen_norm or None,
                    )
                    if tap_delta is not None:
                        _tap_region, dx_pct, dy_pct = tap_delta
                        tap_x_pct = mx_pct + dx_pct
                        tap_y_pct = my_pct + dy_pct
                    score = res["score"]
                    matched = score >= threshold
                    tl_tuple = (int(res["top_left"][0]), int(res["top_left"][1]))
                    sat_fail: str | None = None
                    mean_sat: float | None = None
                    bright_fail: str | None = None
                    tpl_bright: float | None = None
                    patch_bright: float | None = None
                    if matched:
                        ok, tpl_bright, patch_bright, bright_fail = _apply_bright_detail_gate(
                            image_bgr, tpl, tl_tuple
                        )
                        matched = ok
                    if matched and min_sat is not None:
                        ok, mean_sat, sat_fail = _apply_min_saturation_gate(
                            image_bgr, tl_tuple, tw_tpl, th_tpl, min_sat
                        )
                        matched = ok
                    hit: dict[str, Any] = {
                        "matched": matched,
                        "score": score,
                        "score_ncc": res.get("score_ncc"),
                        "score_ncc_second": res.get("score_ncc_second"),
                        "score_color": res.get("score_color"),
                        "threshold": threshold,
                        "top_left": list(res["top_left"]),
                        "template_w": tw_tpl,
                        "template_h": th_tpl,
                        "action": "findIcon",
                        "region": region_name,
                        "resolved_region": resolved_region_name,
                        "resolved_version": region_version_of(entry, reg),
                        "search_region": search_region_name,
                        "tap_x_pct": tap_x_pct,
                        "tap_y_pct": tap_y_pct,
                    }
                    # Always expose match center (before tap offset) for UI/debug.
                    hit["tap_match_x_pct"] = mx_pct
                    hit["tap_match_y_pct"] = my_pct
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
                    if tpl_bright is not None:
                        hit["template_bright_ratio"] = tpl_bright
                        hit["patch_bright_ratio"] = patch_bright
                    if mean_sat is not None:
                        hit["mean_saturation"] = mean_sat
                    if bright_fail or sat_fail:
                        hit["reason"] = bright_fail or sat_fail
                    out[logical_name] = hit
                    continue

                res = match_crop_1to1_at_bbox_percent(image_bgr, tpl, bbox)
            except ValueError as e:
                out[logical_name] = {
                    "matched": False,
                    "reason": "shape_mismatch",
                    "detail": str(e),
                }
                continue

            score = res["score"]
            tl_x = float(res["top_left"][0])
            tl_y = float(res["top_left"][1])
            mx_pct = 100.0 * (tl_x + tw_tpl / 2.0) / wi
            my_pct = 100.0 * (tl_y + th_tpl / 2.0) / hi

            tap_x_pct_1 = mx_pct
            tap_y_pct_1 = my_pct
            tap_delta_1 = _tap_region_delta_pct(
                area_doc,
                region_name,
                rule,
                state_flat=state_flat,
                screen_id=cur_screen_norm or None,
            )
            if tap_delta_1 is not None:
                _tap_region_1, dx_pct_1, dy_pct_1 = tap_delta_1
                tap_x_pct_1 = mx_pct + dx_pct_1
                tap_y_pct_1 = my_pct + dy_pct_1

            matched_1 = score >= threshold
            sat_fail_1: str | None = None
            mean_sat_1: float | None = None
            bright_fail_1: str | None = None
            tpl_bright_1: float | None = None
            patch_bright_1: float | None = None
            tl_tuple_1 = (int(res["top_left"][0]), int(res["top_left"][1]))
            if matched_1:
                ok, tpl_bright_1, patch_bright_1, bright_fail_1 = _apply_bright_detail_gate(
                    image_bgr, tpl, tl_tuple_1
                )
                matched_1 = ok
            if matched_1 and min_sat is not None:
                ok, mean_sat_1, sat_fail_1 = _apply_min_saturation_gate(
                    image_bgr,
                    tl_tuple_1,
                    tw_tpl,
                    th_tpl,
                    min_sat,
                )
                matched_1 = ok

            hit1: dict[str, Any] = {
                "matched": matched_1,
                "score": score,
                "score_ncc": res.get("score_ncc"),
                "score_color": res.get("score_color"),
                "threshold": threshold,
                "top_left": list(res["top_left"]),
                "template_w": tw_tpl,
                "template_h": th_tpl,
                "action": "findIcon",
                "region": region_name,
                "resolved_region": resolved_region_name,
                "resolved_version": region_version_of(entry, reg),
                "tap_x_pct": tap_x_pct_1,
                "tap_y_pct": tap_y_pct_1,
            }
            hit1["tap_match_x_pct"] = mx_pct
            hit1["tap_match_y_pct"] = my_pct
            if tap_delta_1 is not None:
                tap_region_1, dx_pct_1, dy_pct_1 = tap_delta_1
                hit1["tap_region"] = tap_region_1
                hit1["tap_delta_x_pct"] = dx_pct_1
                hit1["tap_delta_y_pct"] = dy_pct_1
            if push_tasks:
                hit1["pushScenario"] = push_tasks
            if set_node_s:
                hit1["set_node"] = set_node_s
            if priority is not None:
                hit1["priority"] = priority
            if min_sat is not None:
                hit1["min_match_saturation"] = min_sat
            if tpl_bright_1 is not None:
                hit1["template_bright_ratio"] = tpl_bright_1
                hit1["patch_bright_ratio"] = patch_bright_1
            if mean_sat_1 is not None:
                hit1["mean_saturation"] = mean_sat_1
            if bright_fail_1 or sat_fail_1:
                hit1["reason"] = bright_fail_1 or sat_fail_1
            out[logical_name] = hit1
            continue

        if action == "color_check":
            region_name = str(rule.get("region") or "").strip()
            pair = _lookup_region(region_name) if region_name else None
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                    "action": "color_check",
                }
                continue
            entry, reg = pair
            bbox = reg.get("bbox")
            if not isinstance(bbox, dict):
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox",
                    "region": region_name,
                    "action": "color_check",
                }
                continue

            want = str(rule.get("type") or reg.get("type") or "").strip().lower()
            if want == "grey":
                want = "gray"
            if want not in {"red", "blue", "green", "gray"}:
                out[logical_name] = {
                    "matched": False,
                    "reason": "invalid_color_type",
                    "region": region_name,
                    "action": "color_check",
                    "want": want,
                }
                continue

            threshold_raw = rule.get("threshold", reg.get("threshold", 0.5))
            try:
                threshold = float(threshold_raw) if threshold_raw is not None else 0.5
            except (TypeError, ValueError):
                threshold = 0.5
            threshold = max(0.0, min(1.0, float(threshold)))

            patch, _patch_tl = patch_bgr_from_bbox_percent(image_bgr, bbox)
            ph, pw = int(patch.shape[0]), int(patch.shape[1])
            resolved_region_name = str(reg.get("name") or "").strip() or region_name
            ref_rel = effective_ocr_for_region(entry, reg)
            if ref_rel:
                crop_path = exported_crop_png(repo_root, ref_rel, resolved_region_name)
                if crop_path.is_file():
                    ref_img = cv2.imread(str(crop_path))
                    if ref_img is not None and ref_img.size > 0:
                        ref_ph, ref_pw = int(ref_img.shape[0]), int(ref_img.shape[1])
                        try:
                            validate_live_bbox_patch_vs_reference_dims(
                                pw, ph, ref_pw, ref_ph, reference_label="exported crop"
                            )
                        except ValueError as exc:
                            out[logical_name] = {
                                "matched": False,
                                "reason": "color_check_crop_size_mismatch",
                                "detail": str(exc),
                                "region": region_name,
                                "action": "color_check",
                                "live_patch_w": pw,
                                "live_patch_h": ph,
                                "ref_crop_w": ref_pw,
                                "ref_crop_h": ref_ph,
                            }
                            continue

            dom, shares = dominant_color_label_bgr(patch)
            share = float(shares.get(dom, 0.0))
            matched = dom == want and share >= threshold

            hit: dict[str, Any] = {
                "matched": matched,
                "action": "color_check",
                "region": region_name,
                "want": want,
                "dominant": dom,
                "share": share,
                "threshold": threshold,
                "shares": shares,
            }
            push_tasks = optional_push_scenario_tasks(rule)
            if push_tasks:
                hit["pushScenario"] = push_tasks
            if set_node_s:
                hit["set_node"] = set_node_s
            if priority is not None:
                hit["priority"] = priority
            out[logical_name] = hit
            continue

        if action == "text":
            region_name = str(rule.get("region") or "").strip()
            threshold = float(rule.get("threshold", 0.7))
            expected = optional_expected_texts(rule)

            pair = _lookup_region(region_name)
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                }
                continue
            entry, reg = pair
            ref_rel = effective_ocr_for_region(entry, reg)
            # ``type: time`` opts the rule into HH:MM:SS / MM:SS parsing so
            # downstream consumers can read ``time_seconds`` directly. Rule
            # ``type:`` wins; otherwise inherit from area.json so a region
            # tagged once in the annotator (``type: time``) doesn't have to
            # be repeated in every overlay rule that targets it. Mirrors the
            # DSL ``ocr`` step's resolution.
            rule_type = str(
                rule.get("type") or reg.get("type") or ""
            ).strip().lower()
            bbox = reg.get("bbox")
            if not isinstance(bbox, dict):
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox",
                    "region": region_name,
                }
                continue

            hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            region_px = _bbox_percent_to_region_px(bbox, wi, hi)
            # ``preprocess`` selects the backend pipeline (``enhance``,
            # ``fast_line``, …). Explicit on the rule wins, otherwise inherit
            # from the area.json region. When nothing is set, the resolver
            # auto-derives ``fast_line`` for ``type: time`` / ``type: integer``
            # regions so countdown / stat reads use Tesseract's single-line mode.
            # ``type: string`` and missing types stay on the full pipeline.
            preprocess = resolve_preprocess(
                explicit=rule.get("preprocess") or reg.get("preprocess"),
                type_hint=rule_type,
            )
            pending_text_rules.append(
                {
                    "logical_name": logical_name,
                    "region_name": region_name,
                    "region_px": region_px,
                    "ref_rel": ref_rel,
                    "rule_type": rule_type,
                    "expected": expected,
                    "threshold": threshold,
                    "preprocess": preprocess,
                    "push_tasks": optional_push_scenario_tasks(rule),
                    "set_node_s": set_node_s,
                    "priority": priority,
                }
            )
            continue

        out[logical_name] = {"matched": False, "reason": "unsupported_action", "action": action}

    if pending_text_rules:
        text_out = await _evaluate_pending_text_rules(
            image_bgr,
            area_doc,
            pending_text_rules,
            state_flat=state_flat,
            screen_id=cur_screen_norm or None,
            ocr_client=ocr_client,
        )
        out.update(text_out)

    return out


async def _evaluate_pending_text_rules(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    pending: list[dict[str, Any]],
    *,
    state_flat: dict[str, Any] | None,
    screen_id: str | None,
    ocr_client: OcrClient | None = None,
) -> dict[str, Any]:
    """Two-phase batched OCR for ``action: text`` overlay rules.

    Phase 1 — one ``ocr_regions`` call for every primary bbox. The
    ``OcrClient`` already deduplicates identical patches within a single batch
    by ``patch_hash``, so a frame with 141 ``page.heroes.unit.name`` cells
    only sends one entry to the backend.

    Replaces the old per-rule ``ocr.ocr_region()`` path: a tick with 130+
    text rules now pays one batched OCR request instead of one per rule.
    """
    from tasks.dsl_scenario_helpers import _parse_hms_to_seconds

    out: dict[str, Any] = {}
    if not pending:
        return out

    from services import get_ocr_client

    ocr = ocr_client if ocr_client is not None else get_ocr_client()
    # Positional region_ids so two rules targeting the same area.json region
    # with different ``expected`` / ``threshold`` don't collide on the same
    # OCR slot — the client's within-call ``patch_hash`` dedup still collapses
    # identical pixels into one backend entry.
    primary_ids = [f"text::{i}" for i in range(len(pending))]
    primary_regions = [p["region_px"] for p in pending]
    primary_preprocess: list[str | None] = [p.get("preprocess") for p in pending]
    try:
        primary_results = await ocr.ocr_regions(
            image_bgr,
            primary_regions,
            region_ids=primary_ids,
            region_preprocess=primary_preprocess if any(primary_preprocess) else None,
        )
    except Exception as e:
        for p in pending:
            out[p["logical_name"]] = {
                "matched": False,
                "reason": "ocr_failed",
                "detail": str(e),
            }
        return out

    primary_by_id = {r.region_id: r for r in primary_results}

    inter: list[dict[str, Any]] = []
    for i, p in enumerate(pending):
        res = primary_by_id.get(primary_ids[i])
        if res is None:
            txt = ""
            conf = 0.0
        else:
            txt = str(res.text or "").strip()
            conf = float(res.confidence or 0.0)

        matched = False
        best: dict[str, object] | None = None
        ocr_source = p["region_name"]
        expected = p["expected"]

        if expected:
            # ``partial=True``: OCR may pick up sibling labels (multi-line
            # popups, level badges next to the prompt). Mirrors what an
            # author writes in ``expected: ["tap anywhere"]`` — they mean
            # "this phrase appears somewhere in the OCR'd content", not
            # "the OCR result equals this phrase verbatim".
            m = fuzzy_match(txt, expected, threshold=p["threshold"], partial=True)
            if m is not None:
                matched = True
                best = {"candidate": m.candidate, "score": m.score}
        else:
            matched = bool(txt)

        inter.append(
            {
                "txt": txt,
                "conf": conf,
                "matched": matched,
                "best": best,
                "ocr_source": ocr_source,
            }
        )

    for i, p in enumerate(pending):
        slot = inter[i]
        matched = bool(slot["matched"])
        txt = str(slot["txt"])
        time_seconds: int | None = None
        if p["rule_type"] == "time":
            parsed = _parse_hms_to_seconds(txt)
            if parsed is not None:
                time_seconds = int(parsed)
                # The presence of a parsed time IS the "matched" signal for
                # time rules — a HH:MM:SS countdown is what we OCR'd for.
                # Override the earlier ``matched=bool(txt)`` so a rule
                # without ``expected:`` still reports matched=False when the
                # timer text was unreadable noise.
                matched = True

        entry: dict[str, Any] = {
            "matched": matched,
            "action": "text",
            "region": p["region_name"],
            "text": txt,
            "confidence": slot["conf"],
            "threshold": p["threshold"],
            "expected": p["expected"],
            "match": slot["best"],
            "ocr_source": slot["ocr_source"],
        }
        if p["rule_type"]:
            entry["type"] = p["rule_type"]
        if time_seconds is not None:
            # Surfaced so the overlay enqueuer can use it as the dynamic
            # ``ttl`` for any ``pushScenario`` entries — see
            # ``_enqueue_push_scenarios_from_overlay``.
            entry["time_seconds"] = time_seconds
        if p["push_tasks"]:
            entry["pushScenario"] = p["push_tasks"]
        if p["set_node_s"]:
            entry["set_node"] = p["set_node_s"]
        if p["priority"] is not None:
            entry["priority"] = p["priority"]
        out[p["logical_name"]] = entry

    return out
