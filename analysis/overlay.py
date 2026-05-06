"""Overlay rules from ``analyze.yaml``, evaluated before screen-specific logic."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from layout.area_lookup import screen_region_by_name
from layout.bbox_percent import bbox_percent_center_xy_pct
from layout.crop_paths import exported_crop_png
from layout.template_match import (
    match_crop_1to1_at_bbox_percent,
    match_template_in_search_roi_bbox_percent,
)


def load_analyze_yaml(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def centers_delta_pct_between_regions(
    area_doc: dict[str, Any],
    from_region: str,
    to_region: str,
) -> tuple[float, float] | None:
    """Vector ``to_center - from_center`` in percent of frame (from ``area.json`` bboxes)."""
    pa = screen_region_by_name(area_doc, from_region)
    pb = screen_region_by_name(area_doc, to_region)
    if pa is None or pb is None:
        return None
    ba = pa[1].get("bbox")
    bb = pb[1].get("bbox")
    if not isinstance(ba, dict) or not isinstance(bb, dict):
        return None
    ax, ay = bbox_percent_center_xy_pct(ba)
    bx, by = bbox_percent_center_xy_pct(bb)
    return bx - ax, by - ay


def evaluate_overlay_rules(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo_root: Path,
    overlay_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Run ordered overlay rules; returns a dict keyed by rule ``name``."""
    out: dict[str, Any] = {}
    for rule in overlay_rules:
        if not isinstance(rule, dict):
            continue
        action = str(rule.get("action") or "").strip()
        logical_name = str(rule.get("name") or "").strip()
        if not logical_name:
            continue

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
                out[logical_name] = {"matched": False, "reason": "missing_bbox_or_ocr"}
                continue

            crop_path = exported_crop_png(repo_root, ref_rel, region_name)
            if not crop_path.is_file():
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_crop_png",
                    "path": str(crop_path.relative_to(repo_root)),
                }
                continue

            tpl = cv2.imread(str(crop_path))
            if tpl is None:
                out[logical_name] = {"matched": False, "reason": "crop_load_failed"}
                continue

            tap_region_name = str(rule.get("tap_region") or "").strip()
            tap_offset_from_match = bool(rule.get("tap_offset_from_match"))
            tap_override_pct: tuple[float, float] | None = None
            tap_delta_pct: tuple[float, float] | None = None

            if tap_offset_from_match:
                if not tap_region_name:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "tap_offset_requires_tap_region",
                        "region": region_name,
                    }
                    continue
                pair_t = screen_region_by_name(area_doc, tap_region_name)
                if pair_t is None:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "unknown_tap_region",
                        "region": region_name,
                        "tap_region": tap_region_name,
                    }
                    continue
                entry_t, _ = pair_t
                if str(entry_t.get("ocr") or "").strip() != ref_rel:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "tap_region_screen_mismatch",
                        "region": region_name,
                        "tap_region": tap_region_name,
                    }
                    continue
                tap_delta_pct = centers_delta_pct_between_regions(
                    area_doc, region_name, tap_region_name
                )
                if tap_delta_pct is None:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "tap_offset_delta_failed",
                        "region": region_name,
                        "tap_region": tap_region_name,
                        "detail": "need bbox on region and tap_region",
                    }
                    continue
            elif tap_region_name:
                pair_t = screen_region_by_name(area_doc, tap_region_name)
                if pair_t is None:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "unknown_tap_region",
                        "region": region_name,
                        "tap_region": tap_region_name,
                    }
                    continue
                entry_t, reg_t = pair_t
                if str(entry_t.get("ocr") or "").strip() != ref_rel:
                    out[logical_name] = {
                        "matched": False,
                        "reason": "tap_region_screen_mismatch",
                        "region": region_name,
                        "tap_region": tap_region_name,
                        "detail": "tap_region must use the same ocr frame as region",
                    }
                    continue
                tap_bbox = reg_t.get("bbox")
                if not isinstance(tap_bbox, dict):
                    out[logical_name] = {
                        "matched": False,
                        "reason": "missing_tap_bbox",
                        "tap_region": tap_region_name,
                        "region": region_name,
                    }
                    continue
                tap_override_pct = bbox_percent_center_xy_pct(tap_bbox)

            hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            tw_tpl = int(tpl.shape[1])
            th_tpl = int(tpl.shape[0])
            search_region_name = str(rule.get("search_region") or "").strip()

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
                    res = match_template_in_search_roi_bbox_percent(image_bgr, tpl, search_bbox)
                    cx_px = res["top_left"][0] + tw_tpl / 2.0
                    cy_px = res["top_left"][1] + th_tpl / 2.0
                    mx_pct = 100.0 * cx_px / wi
                    my_pct = 100.0 * cy_px / hi
                    if tap_delta_pct is not None:
                        ddx, ddy = tap_delta_pct
                        tap_x_pct = mx_pct + ddx
                        tap_y_pct = my_pct + ddy
                    elif tap_override_pct is not None:
                        tap_x_pct, tap_y_pct = tap_override_pct
                    else:
                        tap_x_pct = mx_pct
                        tap_y_pct = my_pct
                    score = res["score"]
                    hit: dict[str, Any] = {
                        "matched": score >= threshold,
                        "score": score,
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
                    if tap_region_name:
                        hit["tap_region"] = tap_region_name
                    if tap_delta_pct is not None:
                        hit["tap_delta_x_pct"] = tap_delta_pct[0]
                        hit["tap_delta_y_pct"] = tap_delta_pct[1]
                        hit["tap_match_x_pct"] = mx_pct
                        hit["tap_match_y_pct"] = my_pct
                    out[logical_name] = hit
                    continue

                res = match_crop_1to1_at_bbox_percent(image_bgr, tpl, bbox)
            except ValueError as e:
                out[logical_name] = {"matched": False, "reason": "shape_mismatch", "detail": str(e)}
                continue

            score = res["score"]
            tl_x = float(res["top_left"][0])
            tl_y = float(res["top_left"][1])
            mx_pct = 100.0 * (tl_x + tw_tpl / 2.0) / wi
            my_pct = 100.0 * (tl_y + th_tpl / 2.0) / hi

            hit1: dict[str, Any] = {
                "matched": score >= threshold,
                "score": score,
                "threshold": threshold,
                "top_left": list(res["top_left"]),
                "template_w": tw_tpl,
                "template_h": th_tpl,
                "action": "findIcon",
                "region": region_name,
            }
            if tap_delta_pct is not None:
                ddx, ddy = tap_delta_pct
                hit1["tap_x_pct"] = mx_pct + ddx
                hit1["tap_y_pct"] = my_pct + ddy
                hit1["tap_region"] = tap_region_name
                hit1["tap_delta_x_pct"] = ddx
                hit1["tap_delta_y_pct"] = ddy
                hit1["tap_match_x_pct"] = mx_pct
                hit1["tap_match_y_pct"] = my_pct
            elif tap_override_pct is not None:
                tx, ty = tap_override_pct
                hit1["tap_x_pct"] = tx
                hit1["tap_y_pct"] = ty
                hit1["tap_region"] = tap_region_name
            out[logical_name] = hit1
            continue

        out[logical_name] = {"matched": False, "reason": "unsupported_action", "action": action}

    return out


def run_overlay_analysis(
    image_bgr: np.ndarray,
    *,
    repo_root: Path,
    analyze_yaml: Path | None = None,
    area_doc: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Load ``references/analyze.yaml`` (unless overridden) and evaluate ``overlay`` rules."""
    cfg_path = (
        analyze_yaml
        if analyze_yaml is not None
        else repo_root / "references" / "analyze.yaml"
    )
    cfg = load_analyze_yaml(cfg_path) if cfg_path.is_file() else {}
    overlay = cfg.get("overlay")
    rules = overlay if isinstance(overlay, list) else []

    if area_doc is None:
        import json

        area_path = repo_root / "area.json"
        area_doc = json.loads(area_path.read_text(encoding="utf-8"))

    return evaluate_overlay_rules(image_bgr, area_doc, repo_root, rules)
