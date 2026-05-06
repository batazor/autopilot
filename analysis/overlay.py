"""Overlay rules from ``analyze.yaml``, evaluated before screen-specific logic."""

from __future__ import annotations

import asyncio
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
    match_patch_bgr_at_top_left,
    match_template_in_search_roi_bbox_percent,
    patch_mean_hsv_saturation,
)
from layout.types import Region
from ocr.client import OcrClient
from ocr.fuzzy import match as fuzzy_match


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _resolve_includes(manifest_path: Path, include: list[object]) -> list[Path]:
    out: list[Path] = []
    for item in include:
        s = str(item or "").strip()
        if not s:
            continue
        p = Path(s)
        if not p.is_absolute():
            p = manifest_path.parent / p
        out.append(p)
    return out


def load_analyze_yaml(path: Path) -> dict[str, Any]:
    """Load analyze.yaml config.

    Supports a "manifest" file with:

    - include: ["analyze_pages/analyze_main_page.yaml", ...]

    In that case, the returned dict merges keys, and concatenates ``overlay`` lists
    from all included files (and from the manifest itself, if present).
    """
    if not path.is_file():
        return {}

    raw = _load_yaml_dict(path)

    overlay_merged: list[dict[str, Any]] = []
    ov = raw.get("overlay")
    if isinstance(ov, list):
        overlay_merged.extend([r for r in ov if isinstance(r, dict)])

    inc = raw.get("include")
    if isinstance(inc, list) and inc:
        for inc_path in _resolve_includes(path, inc):
            if not inc_path.is_file():
                continue
            doc = _load_yaml_dict(inc_path)
            for k, v in doc.items():
                if k == "overlay":
                    continue
                if k not in raw:
                    raw[k] = v
            ov2 = doc.get("overlay")
            if isinstance(ov2, list):
                overlay_merged.extend([r for r in ov2 if isinstance(r, dict)])

    if overlay_merged:
        raw["overlay"] = overlay_merged
    return raw


def _optional_min_match_saturation(rule: dict[str, Any]) -> float | None:
    """YAML ``min_match_saturation`` (0–255): reject match if mean HSV S on live patch is below."""
    v = rule.get("min_match_saturation")
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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


def _optional_fuzzy_threshold(rule: dict[str, Any]) -> float | None:
    v = rule.get("fuzzy_threshold")
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _optional_priority(rule: dict[str, Any]) -> int | None:
    v = rule.get("priority")
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _optional_expected_texts(rule: dict[str, Any]) -> list[str]:
    v = rule.get("expected")
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    s = rule.get("expected_text")
    if s:
        return [str(s)]
    return []


async def evaluate_overlay_rules_async(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo_root: Path,
    overlay_rules: list[dict[str, Any]],
    *,
    current_screen: str | None = None,
) -> dict[str, Any]:
    """Run ordered overlay rules; returns a dict keyed by rule ``name``.

    When *current_screen* is provided, rules that declare a non-empty ``screens``
    list are only evaluated when *current_screen* is in that list.  Rules with no
    ``screens`` key (or an empty list) are treated as **global** and always run
    (e.g. ad/popup dismissers).
    """
    out: dict[str, Any] = {}
    for rule in overlay_rules:
        if not isinstance(rule, dict):
            continue
        set_node = rule.get("set_node")
        set_node_s = str(set_node).strip() if isinstance(set_node, str) else ""
        priority = _optional_priority(rule)
        # Screen filter: skip screen-specific rules when current screen doesn't match.
        rule_screens = rule.get("screens")
        if not rule_screens:
            node = rule.get("node")
            if isinstance(node, str) and node.strip():
                rule_screens = [node.strip()]
        if rule_screens and current_screen not in rule_screens:
            continue
        action = str(rule.get("action") or "").strip()
        logical_name = str(rule.get("name") or "").strip()
        if not logical_name:
            continue

        if action == "findIcon":
            region_name = str(rule.get("region") or "").strip()
            threshold = float(rule.get("threshold", 0.7))
            enqueue_tap = bool(rule.get("enqueue_tap", True))
            pair = screen_region_by_name(area_doc, region_name)
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                    "enqueue_tap": enqueue_tap,
                }
                continue
            entry, reg = pair
            bbox = reg.get("bbox")
            ref_rel = str(entry.get("ocr") or "").strip()
            if not isinstance(bbox, dict) or not ref_rel:
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox_or_ocr",
                    "enqueue_tap": enqueue_tap,
                }
                continue

            crop_path = exported_crop_png(repo_root, ref_rel, region_name)
            if not crop_path.is_file():
                # Auto-export crop from the reference screenshot on demand.
                # Keeps overlay matching working even if `references/crop/` wasn't generated.
                try:
                    ref_path = repo_root / ref_rel
                    if ref_path.is_file():
                        ref_img = cv2.imread(str(ref_path))
                        if ref_img is not None:
                            hr, wr = int(ref_img.shape[0]), int(ref_img.shape[1])
                            region_px = _bbox_percent_to_region_px(bbox, wr, hr)
                            x1 = int(region_px.x1)
                            y1 = int(region_px.y1)
                            x2 = int(region_px.x2)
                            y2 = int(region_px.y2)
                            crop = ref_img[y1:y2, x1:x2]
                            if crop.size > 0:
                                crop_path.parent.mkdir(parents=True, exist_ok=True)
                                cv2.imwrite(str(crop_path), crop)
                except Exception:
                    # Best-effort; fall back to the missing-crop response below.
                    pass

            if not crop_path.is_file():
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_crop_png",
                    "path": str(crop_path.relative_to(repo_root)),
                    "enqueue_tap": enqueue_tap,
                }
                continue

            tpl = cv2.imread(str(crop_path))
            if tpl is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "crop_load_failed",
                    "enqueue_tap": enqueue_tap,
                }
                continue

            tap_region_name = str(rule.get("tap_region") or "").strip()
            tap_offset_from_match = bool(rule.get("tap_offset_from_match"))
            tap_override_pct: tuple[float, float] | None = None
            tap_delta_pct: tuple[float, float] | None = None
            min_sat = _optional_min_match_saturation(rule)

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
                        "threshold": threshold,
                        "top_left": list(res["top_left"]),
                        "template_w": tw_tpl,
                        "template_h": th_tpl,
                        "action": "findIcon",
                        "region": region_name,
                        "search_region": search_region_name,
                        "tap_x_pct": tap_x_pct,
                        "tap_y_pct": tap_y_pct,
                        "enqueue_tap": enqueue_tap,
                    }
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
                out[logical_name] = {
                    "matched": False,
                    "reason": "shape_mismatch",
                    "detail": str(e),
                    "enqueue_tap": enqueue_tap,
                }
                continue

            score = res["score"]
            tl_x = float(res["top_left"][0])
            tl_y = float(res["top_left"][1])
            mx_pct = 100.0 * (tl_x + tw_tpl / 2.0) / wi
            my_pct = 100.0 * (tl_y + th_tpl / 2.0) / hi

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
                "threshold": threshold,
                "top_left": list(res["top_left"]),
                "template_w": tw_tpl,
                "template_h": th_tpl,
                "action": "findIcon",
                "region": region_name,
                "enqueue_tap": enqueue_tap,
            }
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

        if action == "readText":
            region_name = str(rule.get("region") or "").strip()
            threshold = float(rule.get("threshold", 0.7))
            enqueue_tap = bool(rule.get("enqueue_tap", True))
            expected = _optional_expected_texts(rule)
            fuzzy_thr = _optional_fuzzy_threshold(rule)

            pair = screen_region_by_name(area_doc, region_name)
            if pair is None:
                out[logical_name] = {
                    "matched": False,
                    "reason": "unknown_region",
                    "region": region_name,
                    "enqueue_tap": enqueue_tap,
                }
                continue
            _entry, reg = pair
            bbox = reg.get("bbox")
            if not isinstance(bbox, dict):
                out[logical_name] = {
                    "matched": False,
                    "reason": "missing_bbox",
                    "region": region_name,
                    "enqueue_tap": enqueue_tap,
                }
                continue

            hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
            region_px = _bbox_percent_to_region_px(bbox, wi, hi)
            try:
                ocr = OcrClient()
                res = await ocr.ocr_region(image_bgr, region_px)
            except Exception as e:
                out[logical_name] = {
                    "matched": False,
                    "reason": "ocr_failed",
                    "detail": str(e),
                    "enqueue_tap": enqueue_tap,
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
                # No expected text configured: treat "any non-empty text" as a hit.
                matched = bool(txt)

            out[logical_name] = {
                "matched": matched,
                "action": "readText",
                "region": region_name,
                "text": txt,
                "confidence": conf,
                "threshold": threshold,
                "expected": expected,
                "fuzzy_threshold": fuzzy_thr,
                "match": best,
                "enqueue_tap": enqueue_tap,
            }
            if set_node_s:
                out[logical_name]["set_node"] = set_node_s
            if priority is not None:
                out[logical_name]["priority"] = priority
            continue

        out[logical_name] = {"matched": False, "reason": "unsupported_action", "action": action}

    return out


async def run_overlay_analysis(
    image_bgr: np.ndarray,
    *,
    repo_root: Path,
    analyze_yaml: Path | None = None,
    area_doc: dict[str, Any] | None = None,
    current_screen: str | None = None,
) -> dict[str, Any]:
    """Load ``references/analyze.yaml`` (unless overridden) and evaluate ``overlay`` rules.

    Pass *current_screen* (a ``ScreenName`` string value) to skip rules whose
    ``screens`` list does not include the current screen.
    """
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

    return await evaluate_overlay_rules_async(
        image_bgr, area_doc, repo_root, rules, current_screen=current_screen
    )


def run_overlay_analysis_sync(
    image_bgr: np.ndarray,
    *,
    repo_root: Path,
    analyze_yaml: Path | None = None,
    area_doc: dict[str, Any] | None = None,
    current_screen: str | None = None,
) -> dict[str, Any]:
    """Sync wrapper for contexts that cannot await (e.g. some Streamlit pages)."""
    return asyncio.run(
        run_overlay_analysis(
            image_bgr,
            repo_root=repo_root,
            analyze_yaml=analyze_yaml,
            area_doc=area_doc,
            current_screen=current_screen,
        )
    )


def evaluate_overlay_rules(
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo_root: Path,
    overlay_rules: list[dict[str, Any]],
    *,
    current_screen: str | None = None,
) -> dict[str, Any]:
    """Sync wrapper kept for tests and non-async callers.

    Prefer :func:`evaluate_overlay_rules_async` in async contexts.
    """
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            raise RuntimeError(
                "evaluate_overlay_rules() called from an event loop; "
                "use await evaluate_overlay_rules_async(...) instead."
            )
    except RuntimeError:
        # No running loop: safe to asyncio.run
        pass

    return asyncio.run(
        evaluate_overlay_rules_async(
            image_bgr, area_doc, repo_root, overlay_rules, current_screen=current_screen
        )
    )
