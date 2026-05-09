from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from analysis.overlay_rules import (
    centers_delta_pct_between_regions,
    optional_expected_texts,
    optional_fuzzy_threshold,
    optional_min_match_saturation,
    optional_priority,
    optional_push_scenario_tasks,
    optional_ttl_seconds,
    overlay_rule_screen_allowlist,
    resolved_search_region_for_findicon,
)
from layout.area_lookup import screen_region_by_name
from layout.color_bucket import dominant_color_label_bgr
from layout.crop_paths import exported_crop_png
from layout.template_match import (
    match_crop_1to1_at_bbox_percent,
    match_patch_bgr_at_top_left,
    match_template_in_search_roi_bbox_percent,
    patch_mean_hsv_saturation,
)
from layout.types import Region
from ocr.client import OcrClient
from ocr.fuzzy import match as fuzzy_match


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


def _tap_region_delta_pct(
    area_doc: dict[str, Any],
    region_name: str,
    rule: dict[str, Any],
) -> tuple[str, float, float] | None:
    tap_region = str(rule.get("tap_region") or f"{region_name}_tap").strip()
    if not tap_region:
        return None
    delta = centers_delta_pct_between_regions(area_doc, region_name, tap_region)
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
) -> dict[str, Any]:
    """Run ordered overlay rules; returns a dict keyed by rule ``name``."""
    out: dict[str, Any] = {}
    now_mono = time.monotonic()
    cur_screen_norm = (current_screen or "").strip()
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
            if cur_screen_norm:
                if cur_lc not in allowed_lc:
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

        if action == "findIcon":
            region_name = str(rule.get("region") or "").strip()
            threshold = float(rule.get("threshold", 0.7))
            pair = screen_region_by_name(area_doc, region_name)
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                }
                continue
            entry, reg = pair
            bbox = reg.get("bbox")
            ref_rel = str(entry.get("ocr") or "").strip()
            if not isinstance(bbox, dict) or not ref_rel:
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox_or_ocr",
                }
                continue

            crop_path = exported_crop_png(repo_root, ref_rel, region_name)
            if not crop_path.is_file():
                # Auto-export crop from the reference screenshot on demand.
                try:
                    ref_path = repo_root / ref_rel
                    if ref_path.is_file():
                        ref_img = cv2.imread(str(ref_path))
                        if ref_img is not None:
                            hr, wr = int(ref_img.shape[0]), int(ref_img.shape[1])
                            region_px = _bbox_percent_to_region_px(bbox, wr, hr)
                            x1, y1, x2, y2 = _region_to_xyxy(region_px)
                            crop = ref_img[y1:y2, x1:x2]
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

            tpl = cv2.imread(str(crop_path))
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
            search_region_name = resolved_search_region_for_findicon(
                area_doc, region_name, ref_rel, rule
            )

            try:
                if search_region_name:
                    pair_s = screen_region_by_name(area_doc, search_region_name)
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
                    if ref_search != ref_rel:
                        out[logical_name] = {
                            "matched": False,
                            "reason": "search_region_screen_mismatch",
                            "region": region_name,
                            "search_region": search_region_name,
                            "detail": "search_region must use the same ocr frame as region",
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
                    res = match_template_in_search_roi_bbox_percent(
                        image_bgr,
                        tpl,
                        search_bbox,
                        exclude_top_lefts=excl_pts or None,
                        exclude_radius_px=excl_r,
                    )
                    cx_px = res["top_left"][0] + tw_tpl / 2.0
                    cy_px = res["top_left"][1] + th_tpl / 2.0
                    mx_pct = 100.0 * cx_px / wi
                    my_pct = 100.0 * cy_px / hi
                    tap_x_pct = mx_pct
                    tap_y_pct = my_pct
                    tap_delta = _tap_region_delta_pct(area_doc, region_name, rule)
                    if tap_delta is not None:
                        _tap_region, dx_pct, dy_pct = tap_delta
                        tap_x_pct = mx_pct + dx_pct
                        tap_y_pct = my_pct + dy_pct
                    score = res["score"]
                    matched = score >= threshold
                    tl_tuple = (int(res["top_left"][0]), int(res["top_left"][1]))
                    sat_fail: str | None = None
                    mean_sat: float | None = None
                    if matched and min_sat is not None:
                        ok, mean_sat, sat_fail = _apply_min_saturation_gate(
                            image_bgr, tl_tuple, tw_tpl, th_tpl, min_sat
                        )
                        matched = ok
                    hit: dict[str, Any] = {
                        "matched": matched,
                        "score": score,
                        "score_ncc": res.get("score_ncc"),
                        "score_color": res.get("score_color"),
                        "threshold": threshold,
                        "top_left": list(res["top_left"]),
                        "template_w": tw_tpl,
                        "template_h": th_tpl,
                        "action": "findIcon",
                        "region": region_name,
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
                    if mean_sat is not None:
                        hit["mean_saturation"] = mean_sat
                    if sat_fail:
                        hit["reason"] = sat_fail
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
            tap_delta_1 = _tap_region_delta_pct(area_doc, region_name, rule)
            if tap_delta_1 is not None:
                _tap_region_1, dx_pct_1, dy_pct_1 = tap_delta_1
                tap_x_pct_1 = mx_pct + dx_pct_1
                tap_y_pct_1 = my_pct + dy_pct_1

            matched_1 = score >= threshold
            sat_fail_1: str | None = None
            mean_sat_1: float | None = None
            if matched_1 and min_sat is not None:
                ok, mean_sat_1, sat_fail_1 = _apply_min_saturation_gate(
                    image_bgr,
                    (int(res["top_left"][0]), int(res["top_left"][1])),
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
            if mean_sat_1 is not None:
                hit1["mean_saturation"] = mean_sat_1
            if sat_fail_1:
                hit1["reason"] = sat_fail_1
            out[logical_name] = hit1
            continue

        if action == "color_check":
            region_name = str(rule.get("region") or "").strip()
            pair = screen_region_by_name(area_doc, region_name) if region_name else None
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                    "action": "color_check",
                }
                continue
            _entry, reg = pair
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

            hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            region_px = _bbox_percent_to_region_px(bbox, wi, hi)
            x1, y1, x2, y2 = _region_to_xyxy(region_px)
            patch = image_bgr[y1:y2, x1:x2]
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
            fuzzy_thr = optional_fuzzy_threshold(rule)

            pair = screen_region_by_name(area_doc, region_name)
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                }
                continue
            _entry, reg = pair
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
            ocr = OcrClient()
            try:
                res = await ocr.ocr_region(image_bgr, region_px)
            except Exception as e:
                out[logical_name] = {
                    "matched": False,
                    "reason": "ocr_failed",
                    "detail": str(e),
                }
                continue

            txt = str(res.text or "").strip()
            conf = float(res.confidence or 0.0)
            matched = False
            best: dict[str, object] | None = None

            if expected:
                thr = float(fuzzy_thr) if fuzzy_thr is not None else float(threshold)
                m = fuzzy_match(txt, expected, threshold=thr)
                if m is not None:
                    matched = True
                    best = {"candidate": m.candidate, "score": m.score}
            else:
                matched = bool(txt)

            push_tasks = optional_push_scenario_tasks(rule)
            out[logical_name] = {
                "matched": matched,
                "action": "text",
                "region": region_name,
                "text": txt,
                "confidence": conf,
                "threshold": threshold,
                "expected": expected,
                "fuzzy_threshold": fuzzy_thr,
                "match": best,
            }
            if push_tasks:
                out[logical_name]["pushScenario"] = push_tasks
            if set_node_s:
                out[logical_name]["set_node"] = set_node_s
            if priority is not None:
                out[logical_name]["priority"] = priority
            continue

        out[logical_name] = {"matched": False, "reason": "unsupported_action", "action": action}

    return out
