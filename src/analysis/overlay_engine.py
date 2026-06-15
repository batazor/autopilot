from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import cv2

from analysis.overlay_compile import CompiledOverlayPlan, ensure_overlay_plan
from analysis.overlay_rules import (
    centers_delta_pct_between_regions,  # noqa: F401
    overlay_rule_cond_allows,
    resolved_search_region_for_findicon,
)
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region, region_version_of
from layout.color_bucket import dominant_color_label_bgr
from layout.crop_paths import exported_crop_png
from layout.red_dot_detector import has_red_dot_in_bbox_percent  # noqa: F401
from layout.tab_active_detector import (
    TAB_ACTIVE_MAX_MEAN_SATURATION,
    TAB_ACTIVE_MIN_MEAN_VALUE,
    TAB_ACTIVE_MIN_YELLOW_RATIO,
    tab_activity_stats,
    yellow_tab_ratio,
)
from layout.tabs_strip_identifier import discover_tab_templates, identify_tabs_by_template
from layout.tabs_strip_segmenter import detect_tabs_in_strip
from layout.template_match import (
    TemplateMatchResult,  # noqa: F401
    match_crop_1to1_at_bbox_percent,
    match_patch_bgr_at_top_left,  # noqa: F401
    match_template_full_frame_cached,
    match_template_in_search_roi_bbox_percent,
    patch_bgr_from_bbox_percent,
    patch_mean_hsv_saturation,  # noqa: F401
    template_cache_key,
    validate_live_bbox_patch_vs_reference_dims,
)
from layout.types import Region  # noqa: F401
from layout.white_border_detector import (
    WHITE_BORDER_HALO_PX,
    WHITE_BORDER_MAX_MEAN_SATURATION,
    WHITE_BORDER_MIN_INTERIOR_SATURATION_EXCESS,
    WHITE_BORDER_MIN_MEAN_VALUE,
    WHITE_BORDER_MIN_RING_PIXELS,
    has_white_border_in_bbox_percent,
    white_border_halo_stats,
)
from ocr.fuzzy import match as fuzzy_match
from ocr.preprocess import resolve_preprocess

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

    from ocr.client import OcrClient

# Helper functions extracted into sibling modules; re-imported here so every
# historically-importable name on ``analysis.overlay_engine`` keeps working
# (consumers + test monkeypatch targets). The F401-suppressed entries are pure
# re-exports not referenced by the engine body below.
from analysis.overlay_geometry import (
    _bbox_percent_to_region_px,
    _region_to_xyxy,
    _relative_bbox_percent_from_top_left,  # noqa: F401
    _tap_region_delta_pct,
)
from analysis.overlay_red_dot_gate import (
    _apply_findicon_red_dot_gate,  # noqa: F401
    _direct_template_red_dot_bbox,  # noqa: F401
    _finalize_findicon_hit,
    _probe_red_dot_at_template_match,  # noqa: F401
    _probe_red_dot_within_zone,  # noqa: F401
    build_static_red_dot_hit,
)
from analysis.overlay_template_match import (
    _TEMPLATE_CACHE_MAX,  # noqa: F401
    _apply_bright_detail_gate,
    _apply_min_saturation_gate,
    _bright_low_saturation_ratio,
    _hybrid_sliding_matched,
    _load_template_cached,
    _load_template_with_mask_cached,
    _masked_zero_mean_ncc,  # noqa: F401
    _match_direct_template_in_bbox,
    _template_cache,  # noqa: F401
    _template_cache_lock,  # noqa: F401
    _template_mask_cache,  # noqa: F401
)


async def evaluate_overlay_rules_async(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo_root: Path,
    overlay_rules: list[dict[str, Any]] | CompiledOverlayPlan,
    *,
    current_screen: str | None = None,
    rule_eval_state: dict[str, float] | None = None,
    state_flat: dict[str, Any] | None = None,
    ocr_client: OcrClient | None = None,
    instance_id: str | None = None,
    redis_async: Any | None = None,
    frame_gray: np.ndarray | None = None,
) -> dict[str, Any]:
    """Run ordered overlay rules; returns a dict keyed by rule ``name``."""
    plan = ensure_overlay_plan(overlay_rules)
    out: dict[str, Any] = {}
    now_mono = time.monotonic()
    cur_screen_norm = (current_screen or "").strip()
    if not plan.rules:
        return out
    if not any(compiled.screen.allows(cur_screen_norm) for compiled in plan):
        return out
    if frame_gray is None:
        frame_gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

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
    for compiled in plan:
        rule = compiled.raw
        logical_name = compiled.logical_name
        set_node_s = compiled.set_node_s
        priority = compiled.priority
        action = compiled.action

        if not compiled.screen.allows(cur_screen_norm):
            continue
        if not await overlay_rule_cond_allows(
            rule,
            instance_id=instance_id,
            redis_async=redis_async,
            state_flat=state_flat,
        ):
            continue

        ttl_seconds = compiled.ttl_seconds
        if ttl_seconds is not None and rule_eval_state is not None:
            last = rule_eval_state.get(logical_name)
            if last is not None and (now_mono - last) < ttl_seconds:
                out[logical_name] = {
                    "matched": False,
                    "reason": "ttl_throttled",
                    "ttl": ttl_seconds,
                    "next_eval_in": max(0.0, ttl_seconds - (now_mono - last)),
                    "region": compiled.region_name,
                }
                continue
            rule_eval_state[logical_name] = now_mono

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
                    "red_dot_required": want_present,
                }
                continue
            _entry_rd, reg_rd = pair_rd
            tap_delta_rd = _tap_region_delta_pct(
                area_doc,
                region_name_rd,
                rule,
                state_flat=state_flat,
                screen_id=cur_screen_norm or None,
            )
            hit_rd = build_static_red_dot_hit(
                region=region_name_rd,
                region_def=reg_rd,
                image_bgr=image_bgr,
                requirement=want_present,
                tap_delta=tap_delta_rd,
            )
            push_tasks_rd = compiled.push_tasks
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
            push_tasks_ta = compiled.push_tasks
            if push_tasks_ta:
                hit_ta["pushScenario"] = push_tasks_ta
            if set_node_s:
                hit_ta["set_node"] = set_node_s
            if priority is not None:
                hit_ta["priority"] = priority
            out[logical_name] = hit_ta
            continue

        if action == "detectTabs":
            region_name_dt = str(rule.get("region") or "").strip()
            pair_dt = _lookup_region(region_name_dt) if region_name_dt else None
            if pair_dt is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name_dt,
                    "action": "detectTabs",
                }
                continue
            _entry_dt, reg_dt = pair_dt
            bbox_dt = reg_dt.get("bbox")
            if not isinstance(bbox_dt, dict):
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox",
                    "region": region_name_dt,
                    "action": "detectTabs",
                }
                continue

            tabs_dt = detect_tabs_in_strip(image_bgr, bbox_dt)
            # Identify each tab → page_id (which sub-page does this tab navigate
            # to?). Templates auto-discovered from the strip's namespace — the
            # bot then knows not just "tab N has a red dot" but "page X needs work".
            tab_namespace_dt = str(
                rule.get("namespace") or rule.get("tab_namespace") or ""
            ).strip()
            if not tab_namespace_dt and "." in region_name_dt:
                tab_namespace_dt = region_name_dt.split(".", 1)[0].strip()
            min_score_raw_dt = rule.get("template_min_score")
            try:
                min_score_dt = (
                    float(min_score_raw_dt)
                    if min_score_raw_dt is not None
                    else 0.70
                )
            except (TypeError, ValueError):
                min_score_dt = 0.70
            tab_pages_raw_dt = rule.get("tab_pages")
            explicit_tab_pages_dt: list[str] = []
            if isinstance(tab_pages_raw_dt, list):
                explicit_tab_pages_dt = [
                    str(item or "").strip()
                    for item in tab_pages_raw_dt
                    if str(item or "").strip()
                ]
            if explicit_tab_pages_dt:
                tab_ids_dt = {
                    t.index: explicit_tab_pages_dt[t.index]
                    for t in tabs_dt
                    if 0 <= t.index < len(explicit_tab_pages_dt)
                }
            else:
                tab_ids_dt = identify_tabs_by_template(
                    image_bgr,
                    tabs_dt,
                    discover_tab_templates(
                        area_doc,
                        repo_root,
                        bbox_dt,
                        namespace=tab_namespace_dt,
                    ),
                    min_score=min_score_dt,
                )
            tabs_payload = [
                {
                    "index": t.index,
                    "bbox": t.bbox_percent,
                    "active": t.active,
                    "has_red_dot": t.has_red_dot,
                    "color_state": t.color_state,
                    "segment_source": t.segment_source,
                    "page_id": tab_ids_dt.get(t.index),
                }
                for t in tabs_dt
            ]
            active_index = next((t.index for t in tabs_dt if t.active), None)
            any_red_dot = any(t.has_red_dot for t in tabs_dt)
            # Pages that need work: identified tab + has_red_dot + NOT the
            # currently-active page (active page's own scenario clears its own
            # dot — re-pushing it would loop). Preserve left-to-right order so
            # the bot has a stable iteration order.
            red_dot_pages = [
                tab_ids_dt[t.index]
                for t in tabs_dt
                if t.has_red_dot and not t.active and t.index in tab_ids_dt
            ]
            # Tap target: center of the first tab with a red dot (left-to-right),
            # falling back to the active tab. Lets `pushScenario` consumers click
            # the notification directly without re-segmenting.
            tap_tab = next((t for t in tabs_dt if t.has_red_dot), None)
            if tap_tab is None and active_index is not None:
                tap_tab = tabs_dt[active_index]
            img_h_dt, img_w_dt = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            if tap_tab is not None:
                # Tap the capsule-tight rectangle, not the full strip bbox — the
                # jitter box below is sized from it, so a loose height would let
                # taps drift into the padding above/below the tabs.
                tap_box = tap_tab.tap_bbox_percent or tap_tab.bbox_percent
                tap_x_pct_dt = tap_box["x"] + tap_box["width"] / 2.0
                tap_y_pct_dt = tap_box["y"] + tap_box["height"] / 2.0
                # Expose the chosen tab's pixel dimensions so the DSL click path
                # treats it as a sliding-template match: it builds a synthetic
                # bbox around (tap_x_pct, tap_y_pct) sized template_w × template_h
                # and samples a random point inside (15 % inset). Result: clicks
                # land anywhere within the tab, not always dead-centre — looks
                # more like a human tap.
                tap_template_w = max(1, int(round(tap_box["width"] / 100.0 * img_w_dt)))
                tap_template_h = max(1, int(round(tap_box["height"] / 100.0 * img_h_dt)))
            else:
                tap_x_pct_dt = float(bbox_dt.get("x", 0.0)) + float(bbox_dt.get("width", 0.0)) / 2.0
                tap_y_pct_dt = float(bbox_dt.get("y", 0.0)) + float(bbox_dt.get("height", 0.0)) / 2.0
                tap_template_w = 0
                tap_template_h = 0

            active_page_id_dt = (
                tab_ids_dt.get(active_index) if active_index is not None else None
            )
            red_dot_indices_dt = [t.index for t in tabs_dt if t.has_red_dot]
            hit_dt: dict[str, Any] = {
                "matched": any_red_dot,
                "action": "detectTabs",
                "region": region_name_dt,
                "current_screen": cur_screen_norm,
                "tabs": tabs_payload,
                "tab_count": len(tabs_dt),
                "active_index": active_index,
                "active_page_id": active_page_id_dt,
                "any_red_dot": any_red_dot,
                "red_dot_indices": red_dot_indices_dt,
                "red_dot_pages": red_dot_pages,
                "tap_x_pct": tap_x_pct_dt,
                "tap_y_pct": tap_y_pct_dt,
                "tap_match_x_pct": tap_x_pct_dt,
                "tap_match_y_pct": tap_y_pct_dt,
            }
            if tap_template_w > 0 and tap_template_h > 0:
                hit_dt["template_w"] = tap_template_w
                hit_dt["template_h"] = tap_template_h
            push_tasks_dt = compiled.push_tasks
            if bool(rule.get("push_red_dot_pages")) and red_dot_pages:
                inherited_ttl = None
                if push_tasks_dt:
                    inherited_ttl = push_tasks_dt[0].get("ttl")
                push_tasks_dt = [
                    {
                        "type": page_id,
                        "priority": None,
                        "ttl": inherited_ttl,
                        "dsl_scenario": None,
                    }
                    for page_id in red_dot_pages
                ]
                hit_dt["tab_action"] = "push_red_dot_pages"
            elif push_tasks_dt:
                hit_dt["tab_action"] = "push_scenario"
            elif red_dot_indices_dt:
                hit_dt["tab_action"] = "red_dots_no_push"
            else:
                hit_dt["tab_action"] = "none"
            if push_tasks_dt:
                hit_dt["tab_action_targets"] = [
                    str(item.get("type") or item.get("name") or "").strip()
                    for item in push_tasks_dt
                    if isinstance(item, dict)
                ]
            if push_tasks_dt:
                hit_dt["pushScenario"] = push_tasks_dt
            if set_node_s:
                hit_dt["set_node"] = set_node_s
            if priority is not None:
                hit_dt["priority"] = priority
            out[logical_name] = hit_dt
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
            push_tasks_wb = compiled.push_tasks
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
            threshold = compiled.threshold
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

            min_sat = compiled.min_match_saturation
            min_patch_bright_ratio = compiled.min_patch_bright_ratio
            push_tasks = compiled.push_tasks

            direct_template = compiled.direct_template
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
                # Direct templates locate the peak with TM_CCORR_NORMED (required
                # for masks), which is not mean-centered and scores high on bright
                # panels. ``_hybrid_sliding_matched`` requires both that score and
                # the mean-centered masked NCC (``score_ncc``) to clear threshold,
                # and ``_finalize_findicon_hit`` applies the structural gates.
                out[logical_name] = _finalize_findicon_hit(
                    image_bgr=image_bgr,
                    template_bgr=tpl_bgr,
                    res=res,
                    matched=_hybrid_sliding_matched(score, threshold, res),
                    score=score,
                    threshold=threshold,
                    template_w=tw_tpl,
                    template_h=th_tpl,
                    rule=rule,
                    min_sat=min_sat,
                    min_patch_bright_ratio=min_patch_bright_ratio,
                    region_name=region_name,
                    resolved_region_name=resolved_region_name,
                    resolved_version=region_version_of(entry, reg),
                    match_x_pct=mx_pct,
                    match_y_pct=my_pct,
                    tap_delta=None,
                    push_tasks=push_tasks,
                    set_node_s=set_node_s,
                    priority=priority,
                    extra_fields={
                        "score_ncc_second": res.get("score_ncc_second"),
                        "search_region": search_region_name,
                        "match_source": res.get("match_source"),
                        "template": direct_template,
                    },
                )
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

            min_sat = compiled.min_match_saturation
            min_patch_bright_ratio = compiled.min_patch_bright_ratio
            push_tasks = compiled.push_tasks

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
                        image_gray=frame_gray,
                    )
                    cx_px = res["top_left"][0] + tw_tpl / 2.0
                    cy_px = res["top_left"][1] + th_tpl / 2.0
                    mx_pct = 100.0 * cx_px / wi
                    my_pct = 100.0 * cy_px / hi
                    tap_delta = _tap_region_delta_pct(
                        area_doc,
                        region_name,
                        rule,
                        state_flat=state_flat,
                        screen_id=cur_screen_norm or None,
                    )
                    score = res["score"]
                    out[logical_name] = _finalize_findicon_hit(
                        image_bgr=image_bgr,
                        template_bgr=tpl,
                        res=res,
                        matched=_hybrid_sliding_matched(score, threshold, res),
                        score=score,
                        threshold=threshold,
                        template_w=tw_tpl,
                        template_h=th_tpl,
                        rule=rule,
                        min_sat=min_sat,
                        min_patch_bright_ratio=min_patch_bright_ratio,
                        region_name=region_name,
                        resolved_region_name=resolved_region_name,
                        resolved_version=region_version_of(entry, reg),
                        match_x_pct=mx_pct,
                        match_y_pct=my_pct,
                        tap_delta=tap_delta,
                        push_tasks=push_tasks,
                        set_node_s=set_node_s,
                        priority=priority,
                        extra_fields={
                            "score_ncc_second": res.get("score_ncc_second"),
                            "score_color": res.get("score_color"),
                            "score_edge": res.get("score_edge"),
                            "search_region": "full_frame_cache",
                            "match_source": res.get("match_source"),
                            "hash_distance": res.get("hash_distance"),
                        },
                    )
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
                    if compiled.prefer_primary_bbox:
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
                            ok_p = True
                            if ok_b and min_patch_bright_ratio is not None:
                                patch = image_bgr[
                                    tl_cand[1] : tl_cand[1] + th_tpl,
                                    tl_cand[0] : tl_cand[0] + tw_tpl,
                                ]
                                ok_p = (
                                    _bright_low_saturation_ratio(patch)
                                    >= min_patch_bright_ratio
                                )
                            if ok_b and ok_s and ok_p:
                                res = cand

                    if res is None:
                        res = match_template_in_search_roi_bbox_percent(
                            image_bgr,
                            tpl,
                            search_bbox,
                            exclude_top_lefts=excl_pts or None,
                            exclude_radius_px=excl_r,
                            primary_bbox_percent=bbox,
                            image_gray=frame_gray,
                            threshold=threshold,
                        )
                    cx_px = res["top_left"][0] + tw_tpl / 2.0
                    cy_px = res["top_left"][1] + th_tpl / 2.0
                    mx_pct = 100.0 * cx_px / wi
                    my_pct = 100.0 * cy_px / hi
                    tap_delta = _tap_region_delta_pct(
                        area_doc,
                        region_name,
                        rule,
                        state_flat=state_flat,
                        screen_id=cur_screen_norm or None,
                    )
                    score = res["score"]
                    out[logical_name] = _finalize_findicon_hit(
                        image_bgr=image_bgr,
                        template_bgr=tpl,
                        res=res,
                        matched=_hybrid_sliding_matched(score, threshold, res),
                        score=score,
                        threshold=threshold,
                        template_w=tw_tpl,
                        template_h=th_tpl,
                        rule=rule,
                        min_sat=min_sat,
                        min_patch_bright_ratio=min_patch_bright_ratio,
                        region_name=region_name,
                        resolved_region_name=resolved_region_name,
                        resolved_version=region_version_of(entry, reg),
                        match_x_pct=mx_pct,
                        match_y_pct=my_pct,
                        tap_delta=tap_delta,
                        push_tasks=push_tasks,
                        set_node_s=set_node_s,
                        priority=priority,
                        extra_fields={
                            "score_ncc_second": res.get("score_ncc_second"),
                            "score_color": res.get("score_color"),
                            "search_region": search_region_name,
                        },
                    )
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
            tap_delta_1 = _tap_region_delta_pct(
                area_doc,
                region_name,
                rule,
                state_flat=state_flat,
                screen_id=cur_screen_norm or None,
            )
            out[logical_name] = _finalize_findicon_hit(
                image_bgr=image_bgr,
                template_bgr=tpl,
                res=res,
                matched=score >= threshold,
                score=score,
                threshold=threshold,
                template_w=tw_tpl,
                template_h=th_tpl,
                rule=rule,
                min_sat=min_sat,
                min_patch_bright_ratio=min_patch_bright_ratio,
                region_name=region_name,
                resolved_region_name=resolved_region_name,
                resolved_version=region_version_of(entry, reg),
                match_x_pct=mx_pct,
                match_y_pct=my_pct,
                tap_delta=tap_delta_1,
                push_tasks=push_tasks,
                set_node_s=set_node_s,
                priority=priority,
                extra_fields={"score_color": res.get("score_color")},
            )
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
            push_tasks = compiled.push_tasks
            if push_tasks:
                hit["pushScenario"] = push_tasks
            if set_node_s:
                hit["set_node"] = set_node_s
            if priority is not None:
                hit["priority"] = priority
            out[logical_name] = hit
            continue

        if action == "text":
            region_name = compiled.region_name
            threshold = compiled.threshold
            expected = list(compiled.expected)

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
            rule_type = compiled.rule_type_lc or str(reg.get("type") or "").strip().lower()
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
            # auto-derives ``fast_line`` for time / int / integer regions so
            # countdown / stat reads use Tesseract's single-line mode.
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
                    "exact": compiled.exact,
                    "threshold": threshold,
                    "preprocess": preprocess,
                    "push_tasks": compiled.push_tasks,
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
            if p.get("exact"):
                # Screen-identity rules: plain case-insensitive substring, no
                # fuzzy scoring. ``fuzz.partial_ratio`` would let a short title
                # phrase ("Hall of Heroes") match a window inside another
                # screen's noisy OCR and flip the detected node intermittently.
                txt_lc = txt.lower()
                for cand in expected:
                    if cand.lower() in txt_lc:
                        matched = True
                        best = {"candidate": cand, "score": 1.0}
                        break
            else:
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
