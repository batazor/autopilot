"""Run overlay analyzers on the current rolling frame and build a UI-ready report.

Operator tool ("what does the bot currently see?"): mirrors the worker's overlay
pass against the latest rolling preview PNG, returns per-rule matched/score data
plus pre-rendered ``OverlayShape`` rectangles for the Next.js canvas.
"""
from __future__ import annotations

import asyncio
import base64
from typing import Any, TypedDict

import cv2
import numpy as np

from analysis.overlay import run_overlay_analysis_sync
from analysis.overlay_engine import evaluate_overlay_rules_async
from analysis.overlay_manifest import load_merged_analyze_yaml
from analysis.overlay_rules import (
    overlay_rule_screen_allowlist,
    resolved_search_region_for_findicon,
)
from api.services.click_approval_overlay import (
    OverlayCrosshair,
    OverlayRect,
    OverlayShape,
    load_preview_bytes,
)
from config.paths import repo_root
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region
from layout.crop_paths import exported_crop_png, resolve_reference_path
from ui.reference_preview import load_rolling_instance_preview
from ui.views.click_approvals.common import active_player_state_flat, load_area_doc

_STROKE_MATCHED = "#22c55e"
_STROKE_UNMATCHED = "#64748b"
_STROKE_SEARCH_ROI = "#f59e0b"
_STROKE_REGION_BBOX = "#3b82f6"


class OverlayRuleRow(TypedDict):
    """One row in the overlay-test result table."""

    name: str
    node: str
    region: str
    action: str
    search_region: str
    matched: bool
    score: float | None
    threshold: float | None
    reason: str
    notes: str


class OverlayTestResult(TypedDict):
    """Response payload for ``GET /api/instances/{id}/overlay-test``."""

    instance_id: str
    current_screen: str
    active_player: str
    preview: dict[str, Any]
    rules: list[OverlayRuleRow]
    overlays: list[OverlayShape]
    total_rules: int
    matched_count: int


class ProbeCropSide(TypedDict, total=False):
    available: bool
    width: int
    height: int
    label: str
    data_url: str


class ProbeCrops(TypedDict, total=False):
    region: str
    resolved_region: str
    reference_rel: str
    live: ProbeCropSide
    template: ProbeCropSide


class AreaRegionProbeResult(TypedDict):
    """Response payload for a single ``area.json`` region probe."""

    instance_id: str
    current_screen: str
    active_player: str
    selected_region: str
    regions: list[str]
    preview: dict[str, Any]
    result: dict[str, Any] | None
    overlays: list[OverlayShape]
    crops: ProbeCrops | None


def _coerce_float(value: object) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return round(float(value), 4)  # ty: ignore[invalid-argument-type]
    except (TypeError, ValueError):
        return None


def _bbox_pct_to_px(bb: dict[str, Any], w: int, h: int) -> tuple[int, int, int, int]:
    x = float(bb.get("x") or 0.0)
    y = float(bb.get("y") or 0.0)
    bw = float(bb.get("width") or 0.0)
    bh = float(bb.get("height") or 0.0)
    left = max(0, min(w - 1, int(x / 100.0 * w)))
    top = max(0, min(h - 1, int(y / 100.0 * h)))
    right = max(left + 1, min(w, int((x + bw) / 100.0 * w)))
    bottom = max(top + 1, min(h, int((y + bh) / 100.0 * h)))
    return left, top, right, bottom


def _decode_png_to_bgr(png: bytes) -> np.ndarray | None:
    arr = np.frombuffer(png, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    return img if img is not None else None


def _area_region_names(area_doc: dict[str, Any]) -> list[str]:
    """Logical area-region names, including names that only exist in versions."""
    out: set[str] = set()
    for screen in area_doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        sources: list[Any] = [screen.get("regions")]
        sources.extend(v.get("regions") for v in (screen.get("versions") or []) if isinstance(v, dict))
        for source in sources:
            if not isinstance(source, list):
                continue
            for reg in source:
                if not isinstance(reg, dict):
                    continue
                name = str(reg.get("name") or "").strip()
                if name:
                    out.add(name)
    return sorted(out, key=str.lower)


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


def _rule_metadata(
    rules_raw: list[dict[str, Any]],
    *,
    area_doc: dict[str, Any],
    state_flat: dict[str, Any] | None,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """For each rule by name: (effective node, search_region resolved, action)."""
    rule_node: dict[str, str] = {}
    rule_search: dict[str, str] = {}
    rule_action: dict[str, str] = {}
    for r in rules_raw:
        if not isinstance(r, dict):
            continue
        nm = str(r.get("name") or "").strip()
        if not nm:
            continue
        rule_action[nm] = str(r.get("action") or "").strip()
        gate = overlay_rule_screen_allowlist(r)
        if gate:
            rule_node[nm] = gate[0]
        reg_nm = str(r.get("region") or "").strip()
        pair = (
            screen_region_by_name(area_doc, reg_nm, state_flat=state_flat)
            if reg_nm
            else None
        )
        if pair is not None:
            ref_action = effective_ocr_for_region(pair[0], pair[1])
            sr = resolved_search_region_for_findicon(
                area_doc, reg_nm, ref_action, r, state_flat=state_flat
            )
            if sr:
                rule_search[nm] = sr
    return rule_node, rule_search, rule_action


def _evaluate_rules_ignoring_screen_gate(
    image_bgr: np.ndarray,
    *,
    area_doc: dict[str, Any],
    rules_raw: list[dict[str, Any]],
    repo: Any,
    state_flat: dict[str, Any] | None,
) -> dict[str, Any]:
    """Evaluate every rule regardless of its ``screens`` allowlist.

    Strips ``screens`` from each rule copy so the engine can't short-circuit on
    ``current_screen`` mismatch. Useful for operator probes ("would this rule
    match if its gating were lifted?") without touching the worker's behavior.
    """
    rules_unscoped: list[dict[str, Any]] = []
    for r in rules_raw:
        if not isinstance(r, dict):
            continue
        copy = dict(r)
        copy.pop("screens", None)
        rules_unscoped.append(copy)
    return asyncio.run(
        evaluate_overlay_rules_async(
            image_bgr,
            area_doc,
            repo,
            rules_unscoped,
            current_screen=None,
            state_flat=state_flat,
        )
    )


def run_overlay_test(
    *,
    client: Any,
    instance_id: str,
    only_current_screen: bool = False,
    ignore_screen_gate: bool = False,
) -> OverlayTestResult:
    """Run all overlay rules against the latest rolling frame for ``instance_id``.

    ``only_current_screen``: post-filter to rules whose ``screens`` includes the
    live ``current_screen``. Pure UI noise reduction; doesn't change what runs.

    ``ignore_screen_gate``: bypass the engine's ``screens`` short-circuit so every
    rule actually executes (operator "would this match?" probe). Mutually exclusive
    with ``only_current_screen`` (the filter is meaningless when nothing was gated).
    """
    from ui.redis_client import get_instance_state

    inst_state = get_instance_state(client, instance_id) or {}
    current_screen = str(inst_state.get("current_screen") or "").strip()
    active_player = str(inst_state.get("active_player") or "").strip()
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)

    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id, payload=None, source="live"
    )
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)

    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = _decode_png_to_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    repo = repo_root()
    area_doc = load_area_doc(repo / "area.json")
    merged = load_merged_analyze_yaml(repo)
    rules_raw_obj = merged.get("overlay") if isinstance(merged, dict) else None
    rules_raw = (
        [r for r in rules_raw_obj if isinstance(r, dict)]
        if isinstance(rules_raw_obj, list)
        else []
    )
    rule_node, rule_search, rule_action = _rule_metadata(
        rules_raw, area_doc=area_doc, state_flat=state_flat
    )

    rules: list[OverlayRuleRow] = []
    overlays: list[OverlayShape] = []
    matched_count = 0

    if image_bgr is not None and rules_raw:
        if ignore_screen_gate:
            results = _evaluate_rules_ignoring_screen_gate(
                image_bgr,
                area_doc=area_doc,
                rules_raw=rules_raw,
                repo=repo,
                state_flat=state_flat,
            )
        else:
            results = run_overlay_analysis_sync(
                image_bgr,
                repo_root=repo,
                current_screen=current_screen or None,
                state_flat=state_flat,
            )
        for r in rules_raw:
            name = str(r.get("name") or "").strip()
            if not name:
                continue
            payload = results.get(name)
            if not isinstance(payload, dict):
                continue
            node = rule_node.get(name, "")
            if only_current_screen and current_screen and node and node != current_screen:
                continue
            matched = bool(payload.get("matched"))
            if matched:
                matched_count += 1

            region_name = str(payload.get("region") or r.get("region") or "").strip()
            reason = str(payload.get("reason") or "")
            detail = str(payload.get("detail") or "")
            notes_parts = [reason] if reason else []
            if detail and detail != reason:
                notes_parts.append(detail)

            rules.append(
                OverlayRuleRow(
                    name=name,
                    node=node or "",
                    region=region_name,
                    action=rule_action.get(name, ""),
                    search_region=str(
                        payload.get("search_region") or rule_search.get(name, "")
                    ),
                    matched=matched,
                    score=_coerce_float(payload.get("score")),
                    threshold=_coerce_float(payload.get("threshold")),
                    reason=reason,
                    notes=": ".join(notes_parts).strip(),
                )
            )

            if matched and width > 0 and height > 0:
                if payload.get("top_left") is not None:
                    _add_match_rect(
                        overlays,
                        payload=payload,
                        rule_name=name,
                        matched=True,
                        w=width,
                        h=height,
                    )
                    _add_tap_marker_if_any(
                        overlays, payload=payload, w=width, h=height
                    )
                elif region_name:
                    _add_region_bbox_fallback(
                        overlays,
                        region_name=region_name,
                        area_doc=area_doc,
                        state_flat=state_flat,
                        w=width,
                        h=height,
                        rule_name=name,
                        matched=True,
                    )
                # Search ROI is always informative if the rule defined one.
                _add_search_roi(
                    overlays,
                    payload=payload,
                    rule_search_name=rule_search.get(name, ""),
                    area_doc=area_doc,
                    w=width,
                    h=height,
                )

    return OverlayTestResult(
        instance_id=instance_id,
        current_screen=current_screen,
        active_player=active_player,
        preview={
            "available": png is not None,
            "rel": rel,
            "mtime": mtime,
            "width": width,
            "height": height,
        },
        rules=rules,
        overlays=overlays,
        total_rules=len(rules),
        matched_count=matched_count,
    )


def _bgr_crop_to_data_url(fragment: np.ndarray) -> tuple[str, int, int] | None:
    if fragment.size <= 0:
        return None
    ok, enc = cv2.imencode(".png", fragment)
    if not ok:
        return None
    h, w = int(fragment.shape[0]), int(fragment.shape[1])
    b64 = base64.standard_b64encode(enc.tobytes()).decode("ascii")
    return f"data:image/png;base64,{b64}", w, h


def _ensure_fresh_reference_crop(
    *,
    repo: Any,
    ref_rel: str,
    bbox_pct: dict[str, Any],
    crop_path: Any,
    area_mtime: float,
) -> None:
    """Re-export crop when missing or older than area.json / reference PNG."""
    try:
        ref_path = resolve_reference_path(repo, ref_rel)
        ref_mtime = float(ref_path.stat().st_mtime) if ref_path.is_file() else 0.0
        crop_mtime = float(crop_path.stat().st_mtime) if crop_path.is_file() else 0.0
        if crop_path.is_file() and crop_mtime >= max(area_mtime, ref_mtime):
            return
        img = cv2.imread(str(ref_path))
        if img is None:
            return
        hr, wr = int(img.shape[0]), int(img.shape[1])
        left, top, right, bottom = _bbox_pct_to_px(bbox_pct, wr, hr)
        crop = img[top:bottom, left:right]
        if crop.size <= 0:
            return
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(crop_path), crop)
    except Exception:
        return


def _build_region_probe_crops(
    *,
    image_bgr: np.ndarray,
    area_doc: dict[str, Any],
    repo: Any,
    region_name: str,
    state_flat: dict[str, Any] | None,
    payload: dict[str, Any] | None,
) -> ProbeCrops | None:
    """Live screenshot crop vs ``references/crop/`` template (Streamlit parity)."""
    pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
    if pair is None:
        return None
    entry, reg = pair
    bbox = reg.get("bbox")
    if not isinstance(bbox, dict):
        return None

    h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    left, top, right, bottom = _bbox_pct_to_px(bbox, w, h)
    pad = 6
    left = max(0, min(w - 1, left - pad))
    top = max(0, min(h - 1, top - pad))
    right = max(left + 1, min(w, right + pad))
    bottom = max(top + 1, min(h, bottom + pad))

    resolved_region = str(payload.get("resolved_region") or reg.get("name") or region_name).strip()
    ref_rel = effective_ocr_for_region(entry, reg)
    if not ref_rel:
        ref_rel = str(entry.get("ocr") or "").strip()

    live_side: ProbeCropSide = {"available": False, "width": 0, "height": 0, "label": "Live (rolling PNG)"}
    template_side: ProbeCropSide = {
        "available": False,
        "width": 0,
        "height": 0,
        "label": "Template crop",
    }

    try:
        live_frag = image_bgr[top:bottom, left:right].copy()
        live_enc = _bgr_crop_to_data_url(live_frag)
        if live_enc is not None:
            data_url, lw, lh = live_enc
            live_side = {
                "available": True,
                "width": lw,
                "height": lh,
                "label": "Live (rolling PNG)",
                "data_url": data_url,
            }
    except Exception:
        pass

    if ref_rel:
        try:
            area_path = repo / "area.json"
            area_mtime = float(area_path.stat().st_mtime) if area_path.is_file() else 0.0
            crop_path = exported_crop_png(repo, ref_rel, resolved_region)
            _ensure_fresh_reference_crop(
                repo=repo,
                ref_rel=ref_rel,
                bbox_pct=bbox,
                crop_path=crop_path,
                area_mtime=area_mtime,
            )
            if crop_path.is_file():
                tpl = cv2.imread(str(crop_path))
                if tpl is not None:
                    tpl_enc = _bgr_crop_to_data_url(tpl)
                    if tpl_enc is not None:
                        data_url, tw, th = tpl_enc
                        template_side = {
                            "available": True,
                            "width": tw,
                            "height": th,
                            "label": crop_path.name,
                            "data_url": data_url,
                        }
        except Exception:
            pass

    return ProbeCrops(
        region=region_name,
        resolved_region=resolved_region,
        reference_rel=ref_rel,
        live=live_side,
        template=template_side,
    )


def run_area_region_probe(
    *,
    client: Any,
    instance_id: str,
    region: str | None = None,
    threshold: float = 0.9,
) -> AreaRegionProbeResult:
    """Run a one-off ``exist``/``findIcon`` probe for a selected area region."""
    from ui.redis_client import get_instance_state

    inst_state = get_instance_state(client, instance_id) or {}
    current_screen = str(inst_state.get("current_screen") or "").strip()
    active_player = str(inst_state.get("active_player") or "").strip()
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)

    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id, payload=None, source="live"
    )
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)

    width = height = 0
    image_bgr: np.ndarray | None = None
    if png is not None:
        image_bgr = _decode_png_to_bgr(png)
        if image_bgr is not None:
            height, width = int(image_bgr.shape[0]), int(image_bgr.shape[1])

    repo = repo_root()
    area_doc = load_area_doc(repo / "area.json")
    regions = _area_region_names(area_doc)
    selected = (region or "").strip()
    if selected not in regions:
        selected = regions[0] if regions else ""

    payload: dict[str, Any] | None = None
    overlays: list[OverlayShape] = []
    crops: ProbeCrops | None = None
    if image_bgr is not None and selected:
        safe_threshold = max(0.0, min(1.0, float(threshold)))
        rule = {
            "name": f"probe.area.{selected}",
            "region": selected,
            "action": "exist",
            "threshold": safe_threshold,
        }
        try:
            results = asyncio.run(
                evaluate_overlay_rules_async(
                    image_bgr,
                    area_doc,
                    repo,
                    [rule],
                    current_screen=current_screen or None,
                    state_flat=state_flat,
                )
            )
            raw = results.get(str(rule["name"]))
            payload = raw if isinstance(raw, dict) else {}
        except Exception as exc:
            payload = {
                "matched": False,
                "region": selected,
                "action": "findIcon",
                "threshold": safe_threshold,
                "reason": f"{type(exc).__name__}: {exc}",
            }

        if width > 0 and height > 0 and payload is not None:
            _add_probe_search_area(
                overlays,
                payload=payload,
                region_name=selected,
                area_doc=area_doc,
                state_flat=state_flat,
                w=width,
                h=height,
            )
            _add_probe_best_match(
                overlays,
                payload=payload,
                region_name=selected,
                area_doc=area_doc,
                state_flat=state_flat,
                w=width,
                h=height,
            )
            _add_tap_marker_if_any(overlays, payload=payload, w=width, h=height)
            crops = _build_region_probe_crops(
                image_bgr=image_bgr,
                area_doc=area_doc,
                repo=repo,
                region_name=selected,
                state_flat=state_flat,
                payload=payload,
            )

    return AreaRegionProbeResult(
        instance_id=instance_id,
        current_screen=current_screen,
        active_player=active_player,
        selected_region=selected,
        regions=regions,
        preview={
            "available": png is not None,
            "rel": rel,
            "mtime": mtime,
            "width": width,
            "height": height,
        },
        result=payload,
        overlays=overlays,
        crops=crops,
    )


def load_overlay_test_image(instance_id: str) -> tuple[bytes | None, str, float | None]:
    """Return the rolling preview PNG bytes (live source)."""
    png, rel, mtime = load_preview_bytes(
        instance_id=instance_id, payload=None, source="live"
    )
    if png is None:
        png, rel, mtime = load_rolling_instance_preview(instance_id)
    return png, rel, mtime
