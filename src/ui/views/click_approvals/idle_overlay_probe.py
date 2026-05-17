from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

import cv2
import streamlit as st

from config.module_registry import module_scope_options
from layout.area_lookup import screen_region_by_name
from layout.area_versions import effective_ocr_for_region
from layout.crop_paths import exported_crop_png
from layout.red_dot_detector import has_red_dot_in_bbox_percent
from layout.tab_active_detector import (
    TAB_ACTIVE_MAX_MEAN_SATURATION,
    TAB_ACTIVE_MIN_MEAN_VALUE,
    is_tab_active_in_bbox_percent,
    tab_activity_stats,
)
from layout.template_match import patch_bgr_from_bbox_percent
from layout.white_border_detector import (
    WHITE_BORDER_MAX_MEAN_SATURATION,
    WHITE_BORDER_MIN_INTERIOR_SATURATION_EXCESS,
    WHITE_BORDER_MIN_MEAN_VALUE,
    WHITE_BORDER_MIN_RING_PIXELS,
    has_white_border_in_bbox_percent,
    white_border_halo_stats,
)
from ui.ia_overlay_executor import analyzer_events_key, analyzer_scope_key, analyzer_status_key
from ui.pipeline.data import (
    clear_pipeline_overlay_cache_entries,
    force_nonce,
    get_or_build_pipeline_cache,
)
from ui.pipeline.overlay_viz import (
    annotate_overlay_layers,
    detector_color,
    draw_bbox_pct,
    maybe_downscale_for_ui,
)
from ui.preview_display import png_bytes_fitted
from ui.redis_client import get_instance_state

from .common import labeling_query_ref_from_area_ocr
from .ctx import ClickApprovalsCtx

_ACTION_TYPES: tuple[str, ...] = (
    "findIcon",
    "color_check",
    "text",
    "red_dot",
    "tab_active",
    "white_border",
)


def _fmt_ratio(value: object) -> str:
    try:
        f = float(value) if isinstance(value, (int, float, str, bytes, bytearray)) else float(str(value))
        return f"{f:.3f}"
    except (TypeError, ValueError):
        return "—"


def _pct_bbox_to_px_rect(bb: dict[str, Any], w: int, h: int) -> tuple[int, int, int, int]:
    x = float(bb.get("x") or 0.0)
    y = float(bb.get("y") or 0.0)
    bw = float(bb.get("width") or 0.0)
    bh = float(bb.get("height") or 0.0)
    left = max(0, min(w - 1, int(x / 100.0 * w)))
    top = max(0, min(h - 1, int(y / 100.0 * h)))
    right = max(left + 1, min(w, int((x + bw) / 100.0 * w)))
    bottom = max(top + 1, min(h, int((y + bh) / 100.0 * h)))
    return left, top, right, bottom


def _area_region_names(area_doc: dict[str, Any]) -> list[str]:
    """Logical region names visible in the probe selector.

    Walks every base + ``versions[].regions[]`` block — includes version-only
    region names (e.g. a button that exists only in v2). ``screen_region_by_name``
    resolves picks against the active player's state so version selection is
    automatic.
    """
    out: set[str] = set()
    for screen in area_doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        for source in (screen.get("regions"), *(
            v.get("regions") for v in (screen.get("versions") or []) if isinstance(v, dict)
        )):
            if not isinstance(source, list):
                continue
            for reg in source:
                if not isinstance(reg, dict):
                    continue
                name = str(reg.get("name") or "").strip()
                if name:
                    out.add(name)
    return sorted(out, key=str.lower)


def _redis_text(value: Any) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value or "")


def _render_analyzer_scope_controls(
    *,
    ctx: ClickApprovalsCtx,
    client: Any,
    instance_id: str,
) -> str:
    scope_key = analyzer_scope_key(instance_id)
    raw_scope = _redis_text(client.get(scope_key)).strip() or "disabled"
    options = [("disabled", "Disabled")]
    options.extend(module_scope_options(ctx.repo_root))
    option_values = [value for value, _label in options]
    if raw_scope not in option_values:
        raw_scope = "disabled"
    labels = dict(options)
    widget_key = f"ia_analyzer_scope::{instance_id}"
    if st.session_state.get(widget_key) != raw_scope:
        st.session_state[widget_key] = raw_scope

    def _save_scope() -> None:
        client.set(scope_key, st.session_state.get(widget_key, "disabled"))
        clear_pipeline_overlay_cache_entries(instance_id)
        st.session_state["pipeline_force_refresh_nonce"] = force_nonce() + 1

    selected = st.selectbox(
        "Analyzer module scope",
        option_values,
        index=option_values.index(raw_scope),
        format_func=lambda value: labels.get(value, value),
        key=widget_key,
        on_change=_save_scope,
        help=(
            "IA Editor runs overlay analyzer only for this module scope. "
            "Pushed scenarios still execute through the normal queue and approvals."
        ),
    )

    raw_status = client.get(analyzer_status_key(instance_id))
    status: dict[str, Any] = {}
    if raw_status:
        try:
            status = json.loads(_redis_text(raw_status))
        except json.JSONDecodeError:
            status = {}
    if status:
        skipped = str(status.get("skipped") or "").strip()
        raw_matched = status.get("matched")
        matched = raw_matched if isinstance(raw_matched, list) else []
        raw_pushed = status.get("pushed")
        pushed = raw_pushed if isinstance(raw_pushed, list) else []
        raw_throttled = status.get("throttled")
        throttled = raw_throttled if isinstance(raw_throttled, list) else []
        bits: list[str] = [f"scope `{status.get('scope') or 'disabled'}`"]
        if skipped:
            bits.append(f"skipped `{skipped}`")
        else:
            bits.append(f"matched `{len(matched)}`")
            bits.append(f"pushed `{len(pushed)}`")
            bits.append(f"throttled `{len(throttled)}`")
        st.caption("Analyzer: " + " · ".join(bits))
        if matched or pushed or throttled:
            with st.expander("Recent analyzer result", expanded=False):
                if matched:
                    st.markdown("**Matched rules**")
                    for row in matched[:8]:
                        st.caption(
                            f"`{row.get('rule')}` · `{row.get('region')}` · "
                            f"push `{', '.join(row.get('pushScenario') or []) or '—'}`"
                        )
                if pushed:
                    st.markdown("**Pushed scenarios**")
                    for row in pushed[:8]:
                        st.caption(f"`{row.get('task_id')}` · `{row.get('task_type')}`")
                if throttled:
                    st.markdown("**Throttled pushes**")
                    for row in throttled[:8]:
                        st.caption(f"`{row.get('scenario')}` · ttl `{row.get('ttl')}`s")

    raw_events = client.lrange(analyzer_events_key(instance_id), 0, 2)
    if raw_events:
        with st.expander("Analyzer event history", expanded=False):
            for raw in raw_events:
                try:
                    event = json.loads(_redis_text(raw))
                except json.JSONDecodeError:
                    continue
                raw_pushed = event.get("pushed")
                pushed = raw_pushed if isinstance(raw_pushed, list) else []
                raw_matched = event.get("matched")
                matched = raw_matched if isinstance(raw_matched, list) else []
                st.caption(
                    f"`{event.get('scope')}` · matched `{len(matched)}` · pushed `{len(pushed)}`"
                )
    return selected


def _probe_area_region_ocr(
    *,
    pay: dict[str, Any],
    image_bgr: Any,
    reg: dict[str, Any],
    instance_id: str,
) -> None:
    """Run OCR on a live area-region crop and populate ``pay`` in place.

    Streamlit reruns the panel on every interaction; cache by crop hash so
    local OCR isn't hit on unrelated widget toggles.
    """
    bbox = reg.get("bbox")
    if not isinstance(bbox, dict):
        return
    h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    L, T, R, B = _pct_bbox_to_px_rect(bbox, w, h)
    if R <= L or B <= T:
        return
    crop = image_bgr[T:B, L:R]
    if crop.size <= 0:
        return

    digest = hashlib.md5(crop.tobytes()).hexdigest()
    region_name = str(pay.get("region") or "")
    cache_key = f"idle_ocr_probe::{instance_id}::{region_name}::{digest}"
    cached = st.session_state.get(cache_key)
    if cached is None:
        from config.loader import load_settings
        from layout.types import Region as LayoutRegion
        from ocr.client import OcrClient

        try:
            res = asyncio.run(
                OcrClient(load_settings()).ocr_region(
                    image_bgr, LayoutRegion(L, T, R - L, B - T)
                )
            )
            cached = {
                "ok": True,
                "text": str(getattr(res, "text", "") or "").strip(),
                "confidence": float(getattr(res, "confidence", 0.0) or 0.0),
            }
        except Exception as exc:
            cached = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        st.session_state[cache_key] = cached

    if not cached.get("ok"):
        pay["reason"] = f"ocr_failed: {cached.get('error', '')}"
        return

    txt = str(cached.get("text") or "")
    conf = float(cached.get("confidence") or 0.0)
    try:
        thr = float(pay.get("threshold") or 0.0)
    except (TypeError, ValueError):
        thr = 0.0
    pay["text"] = txt
    pay["confidence"] = conf
    pay["matched"] = bool(txt) and conf >= thr


_EXIST_PROBE_FIELDS: tuple[str, ...] = (
    "matched",
    "score",
    "score_ncc",
    "score_ncc_second",
    "score_color",
    "threshold",
    "search_region",
    "resolved_region",
    "resolved_version",
    "reason",
    "detail",
    "template_bright_ratio",
    "patch_bright_ratio",
    "mean_saturation",
    "top_left",
    "template_w",
    "template_h",
)


def _probe_area_region_exist(
    *,
    pay: dict[str, Any],
    image_bgr: Any,
    area_doc: dict[str, Any],
    repo_root: Any,
    region_name: str,
    state_flat: dict[str, Any] | None,
    instance_id: str,
    current_screen: str | None,
) -> None:
    """Run findIcon match for an area-region with ``action: exist`` and merge
    the engine row into ``pay`` so the score / threshold strip lights up.

    Uses the same ``evaluate_overlay_rules_async`` the worker uses — the engine
    converts ``exist`` to ``findIcon`` internally and applies `isSearch`,
    bright-detail, and saturation gates — so the probe verdict matches what a
    DSL ``match:`` step would see.
    """
    bbox = None
    pair = screen_region_by_name(area_doc, region_name, state_flat=state_flat)
    if pair is not None:
        bbox = pair[1].get("bbox")
    if not isinstance(bbox, dict):
        return

    h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    L, T, R, B = _pct_bbox_to_px_rect(bbox, w, h)
    if R <= L or B <= T:
        return
    crop = image_bgr[T:B, L:R]
    if crop.size <= 0:
        return

    digest = hashlib.md5(crop.tobytes()).hexdigest()
    cache_key = f"idle_exist_probe::{instance_id}::{region_name}::{digest}"
    cached = st.session_state.get(cache_key)
    if cached is None:
        try:
            threshold = float(pay.get("threshold") or 0.9)
        except (TypeError, ValueError):
            threshold = 0.9
        rule = {
            "name": f"probe.area.{region_name}",
            "region": region_name,
            "action": "exist",
            "threshold": threshold,
        }
        try:
            from tasks import dsl_scenario as _dsl

            out = asyncio.run(
                _dsl.evaluate_overlay_rules_async(
                    image_bgr,
                    area_doc,
                    repo_root,
                    [rule],
                    current_screen=current_screen,
                    state_flat=state_flat,
                )
            )
            row = out.get(str(rule["name"]))
            cached = {"ok": True, "row": row if isinstance(row, dict) else {}}
        except Exception as exc:
            cached = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        st.session_state[cache_key] = cached

    if not cached.get("ok"):
        pay["reason"] = f"exist_probe_failed: {cached.get('error', '')}"
        return

    row_raw = cached.get("row")
    row: dict[str, Any] = row_raw if isinstance(row_raw, dict) else {}
    for k in _EXIST_PROBE_FIELDS:
        if k in row:
            pay[k] = row[k]


def _ensure_fresh_reference_crop(
    *,
    repo_root: Any,
    ref_rel: str,
    region_name: str,
    bbox_pct: dict[str, Any],
    crop_path: Any,
    area_mtime: float,
) -> None:
    """Ensure `references/crop/...` exists and matches latest bbox.

    Re-exports when crop is missing or older than area.json / reference PNG.
    """
    del region_name
    try:
        ref_path = repo_root / ref_rel
        ref_mtime = float(ref_path.stat().st_mtime) if ref_path.is_file() else 0.0
        crop_mtime = float(crop_path.stat().st_mtime) if crop_path.is_file() else 0.0
        need = (not crop_path.is_file()) or (crop_mtime < max(area_mtime, ref_mtime))
        if not need:
            return
        img = cv2.imread(str(ref_path))
        if img is None:
            return
        hr, wr = int(img.shape[0]), int(img.shape[1])
        x = float(bbox_pct.get("x") or 0.0)
        y = float(bbox_pct.get("y") or 0.0)
        bw = float(bbox_pct.get("width") or 0.0)
        bh = float(bbox_pct.get("height") or 0.0)
        L = max(0, min(wr - 1, int(round(x / 100.0 * wr))))
        T = max(0, min(hr - 1, int(round(y / 100.0 * hr))))
        R = max(L + 1, min(wr, int(round((x + bw) / 100.0 * wr))))
        B = max(T + 1, min(hr, int(round((y + bh) / 100.0 * hr))))
        crop = img[T:B, L:R]
        if crop.size <= 0:
            return
        crop_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(crop_path), crop)
    except Exception:
        return


def _coerce_float(value: object) -> float | None:
    try:
        if value is None or str(value).strip() == "":
            return None
        if isinstance(value, (int, float, str, bytes, bytearray)):
            return float(value)
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _render_metrics_color_check(pay: dict[str, Any]) -> None:
    matched = bool(pay.get("matched"))
    want = str(pay.get("want") or "").strip().lower()
    dom = str(pay.get("dominant") or "").strip().lower()
    share_f = _coerce_float(pay.get("share"))
    thr_f = _coerce_float(pay.get("threshold"))
    ok_eval = share_f is not None and thr_f is not None

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        if not ok_eval:
            txt = "—"
            color = "#9aa0a6"
        else:
            txt = "yes" if matched else "no"
            color = "#16a34a" if matched else "#dc2626"
        st.markdown(
            f"""
            <div>
              <div style="font-size: 0.85rem; opacity: 0.75;">
                dominant == want &amp; share ≥ threshold
              </div>
              <div style="font-size: 1.75rem; font-weight: 650;
                          line-height: 1.2; color: {color};">
                {txt}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m2:
        st.metric("Dominant", dom or "—")
    with m3:
        st.metric("Want", want or "—")
    with m4:
        st.metric(
            "Share / threshold",
            f"{share_f:.3f} / {thr_f:.3f}" if ok_eval else "—",
        )


def _render_metrics_text(pay: dict[str, Any], *, instance_id: str, sel: str) -> None:
    matched = bool(pay.get("matched"))
    txt = str(pay.get("text") or "").strip()
    conf_f = _coerce_float(pay.get("confidence"))
    m1, m2, m3 = st.columns([1, 1, 2])
    with m1:
        st.metric("Matched", "yes" if matched else "no")
    with m2:
        st.metric("Confidence", f"{conf_f:.4f}" if conf_f is not None else "—")
    with m3:
        st.text_input(
            "OCR text",
            value=txt,
            disabled=True,
            key=f"ovl_text::{instance_id}::{sel}",
        )


def _render_metrics_red_dot(pay: dict[str, Any]) -> None:
    matched = bool(pay.get("matched"))
    want_present = bool(pay.get("want_dot_present"))
    present = bool(pay.get("red_dot_present"))
    m1, m2, m3 = st.columns(3)
    with m1:
        fin_color = "#16a34a" if matched else "#dc2626"
        st.markdown(
            f"""
            <div>
              <div style="font-size: 0.85rem; opacity: 0.75;">Matched</div>
              <div style="font-size: 1.75rem; font-weight: 650;
                          line-height: 1.2; color: {fin_color};">
                {"yes" if matched else "no"}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m2:
        st.metric("Red dot present", "yes" if present else "no")
    with m3:
        st.metric("Want present", "yes" if want_present else "no")


def _render_metrics_tab_active(pay: dict[str, Any]) -> None:
    matched = bool(pay.get("matched"))
    want_active = bool(pay.get("want_tab_active"))
    active = bool(pay.get("tab_active"))
    s_f = _coerce_float(pay.get("mean_saturation"))
    v_f = _coerce_float(pay.get("mean_value"))
    s_max = _coerce_float(pay.get("max_mean_saturation"))
    v_min = _coerce_float(pay.get("min_mean_value"))
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        fin_color = "#16a34a" if matched else "#dc2626"
        st.markdown(
            f"""
            <div>
              <div style="font-size: 0.85rem; opacity: 0.75;">Matched</div>
              <div style="font-size: 1.75rem; font-weight: 650;
                          line-height: 1.2; color: {fin_color};">
                {"yes" if matched else "no"}
              </div>
              <div style="font-size: 0.8rem; opacity: 0.75; margin-top: 4px;">
                tab active: {"yes" if active else "no"} · want: {"yes" if want_active else "no"}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m2:
        st.metric(
            "Mean S / max",
            f"{s_f:.1f} / {s_max:.0f}"
            if (s_f is not None and s_max is not None)
            else "—",
        )
    with m3:
        st.metric(
            "Mean V / min",
            f"{v_f:.1f} / {v_min:.0f}"
            if (v_f is not None and v_min is not None)
            else "—",
        )
    with m4:
        st.metric("Active", "yes" if active else "no")


def _render_metrics_white_border(pay: dict[str, Any]) -> None:
    matched = bool(pay.get("matched"))
    want_border = bool(pay.get("want_white_border"))
    present = bool(pay.get("white_border_present"))
    halo_s = _coerce_float(pay.get("halo_saturation"))
    halo_v = _coerce_float(pay.get("halo_value"))
    excess = _coerce_float(pay.get("interior_saturation_excess"))
    max_s = _coerce_float(pay.get("max_mean_saturation"))
    min_v = _coerce_float(pay.get("min_mean_value"))
    min_ex = _coerce_float(pay.get("min_interior_saturation_excess"))
    ring_count = pay.get("ring_count")
    min_ring = pay.get("min_ring_pixels")

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        fin_color = "#16a34a" if matched else "#dc2626"
        st.markdown(
            f"""
            <div>
              <div style="font-size: 0.85rem; opacity: 0.75;">Matched</div>
              <div style="font-size: 1.75rem; font-weight: 650;
                          line-height: 1.2; color: {fin_color};">
                {"yes" if matched else "no"}
              </div>
              <div style="font-size: 0.8rem; opacity: 0.75; margin-top: 4px;">
                border: {"present" if present else "absent"} · want: {"yes" if want_border else "no"}
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m2:
        st.metric(
            "Halo S / max",
            f"{halo_s:.1f} / {max_s:.0f}"
            if (halo_s is not None and max_s is not None)
            else "—",
        )
    with m3:
        st.metric(
            "Halo V / min",
            f"{halo_v:.1f} / {min_v:.0f}"
            if (halo_v is not None and min_v is not None)
            else "—",
        )
    with m4:
        try:
            ring_str = f"{int(ring_count)} / {int(min_ring)}"  # ty: ignore[invalid-argument-type]
        except (TypeError, ValueError):
            ring_str = "—"
        excess_str = f"{excess:+.1f}" if excess is not None else "—"
        excess_min = f"≥ {min_ex:.0f}" if min_ex is not None else "—"
        st.metric(
            "Inner−halo S / min · ring px / min",
            f"{excess_str} ({excess_min}) · {ring_str}",
        )


def _render_metrics_score(pay: dict[str, Any]) -> None:
    matched = bool(pay.get("matched"))
    score_f = _coerce_float(pay.get("score"))
    thr_f = _coerce_float(pay.get("threshold"))
    gap_s: str | None = None
    if score_f is not None and thr_f is not None:
        gap_s = f"{score_f - thr_f:+.4f}"
    score_ge_thr = (
        score_f is not None and thr_f is not None and score_f >= thr_f
    )

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        ok_eval = score_f is not None and thr_f is not None
        if not ok_eval:
            fin_txt, fin_color = "—", "#9aa0a6"
            thr_line = ""
        else:
            # ``matched`` is false when post-score gates fail (peak uniqueness, bright detail,
            # saturation) even if combined score is above ``threshold`` — do not label that as
            # "below threshold".
            fin_txt = "yes" if matched else "no"
            fin_color = "#16a34a" if matched else "#dc2626"
            thr_yes = "yes" if score_ge_thr else "no"
            thr_color = "#16a34a" if score_ge_thr else "#dc2626"
            thr_line = (
                f'<div style="font-size: 0.8rem; margin-top: 6px; opacity: 0.9;">'
                f'<span style="opacity: 0.75;">score ≥ thr</span> '
                f'<span style="font-weight: 650; color: {thr_color};">{thr_yes}</span>'
                f"</div>"
            )
        st.markdown(
            f"""
            <div>
              <div style="font-size: 0.85rem; opacity: 0.75;">Matched (after gates)</div>
              <div style="font-size: 1.75rem; font-weight: 650;
                          line-height: 1.2; color: {fin_color};">
                {fin_txt}
              </div>
              {thr_line}
            </div>
            """,
            unsafe_allow_html=True,
        )
    with m2:
        st.metric("Score", f"{score_f:.4f}" if score_f is not None else "—")
    with m3:
        st.metric("Threshold", f"{thr_f:.4f}" if thr_f is not None else "—")
    with m4:
        st.metric("Score − thr", gap_s if gap_s is not None else "—")


def _render_rule_metrics(
    *,
    act: str,
    pay: dict[str, Any],
    instance_id: str,
    sel: str,
) -> None:
    if act == "color_check":
        _render_metrics_color_check(pay)
    elif act == "text":
        _render_metrics_text(pay, instance_id=instance_id, sel=sel)
    elif act == "red_dot":
        _render_metrics_red_dot(pay)
    elif act == "tab_active":
        _render_metrics_tab_active(pay)
    elif act == "white_border":
        _render_metrics_white_border(pay)
    else:
        _render_metrics_score(pay)


def _render_rule_info_line(
    *,
    pay: dict[str, Any],
    rule_search_name: str,
    sel_logical: str,
    is_area_region: bool,
    act: str,
    nd: str,
) -> None:
    del sel_logical
    sr_line = str(pay.get("search_region") or rule_search_name or "").strip()
    resolved_line = str(pay.get("resolved_region") or "").strip()
    resolved_ver = str(pay.get("resolved_version") or "").strip()
    region_line = str(pay.get("region") or "").strip()
    if resolved_line and resolved_line != region_line:
        region_line = f"{region_line or '—'} → {resolved_line}"
    if resolved_ver:
        region_line = f"{region_line or '—'} (`{resolved_ver}`)"
    st.markdown(
        f"**Region:** `{region_line or '—'}` · "
        f"**action:** `{act or '—'}` · "
        f"**YAML screens:** `{nd or '(global)'}` · "
        f"**search_region:** `{sr_line or '—'}`"
    )
    if is_area_region:
        st.info(
            "This is an `area.json` region, not an overlay rule. "
            "It can still be used by DSL steps such as `ocr`, `match`, or `click`."
        )
    reason = str(pay.get("reason") or "").strip()
    detail = str(pay.get("detail") or "").strip()
    bits = [reason] if reason else []
    if detail and detail != reason:
        bits.append(detail)
    if bits:
        st.caption(" · ".join(bits))
    if "template_bright_ratio" in pay or "patch_bright_ratio" in pay:
        st.caption(
            "Bright detail ratio · "
            f"template `{_fmt_ratio(pay.get('template_bright_ratio'))}` · "
            f"live `{_fmt_ratio(pay.get('patch_bright_ratio'))}`"
        )


def _render_detector_block(
    *,
    title: str,
    matched: bool,
    primary: str,
    extras: list[tuple[str, str]],
    caption: str = "",
) -> None:
    color = "#16a34a" if matched else "#dc2626"
    with st.container(border=True):
        st.markdown(f"**{title}**")
        cols = st.columns([1.2] + [1] * len(extras))
        with cols[0]:
            st.markdown(
                f"""
                <div>
                  <div style="font-size: 0.85rem; opacity: 0.75;">Detector</div>
                  <div style="font-size: 1.75rem; font-weight: 650;
                              line-height: 1.2; color: {color};">
                    {primary}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        for (label, value), col in zip(extras, cols[1:], strict=False):
            with col:
                st.metric(label, value)
        if caption:
            st.caption(caption)


def _cached_detector_run(
    *,
    image_bgr: Any,
    bbox: dict[str, Any],
    instance_id: str,
    region_name: str,
) -> dict[str, Any]:
    """Run all three detectors once per (rolling PNG, region) and memoize.

    Streamlit reruns the whole panel on every widget interaction; without this
    cache the detectors recompute each time a checkbox is toggled.
    """
    h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    L, T, R, B = _pct_bbox_to_px_rect(bbox, w, h)
    digest = "empty"
    if R > L and B > T:
        crop = image_bgr[T:B, L:R]
        if crop.size > 0:
            digest = hashlib.md5(crop.tobytes()).hexdigest()
    cache_key = f"idle_detector_probe::{instance_id}::{region_name}::{digest}"
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict):
        return cached

    try:
        red_dot_present = bool(has_red_dot_in_bbox_percent(image_bgr, bbox))
    except Exception:
        red_dot_present = False

    try:
        patch_ta, _ = patch_bgr_from_bbox_percent(image_bgr, bbox)
        mean_s, mean_v = tab_activity_stats(patch_ta)
    except Exception:
        mean_s, mean_v = 0.0, 0.0
    try:
        tab_active_present = bool(is_tab_active_in_bbox_percent(image_bgr, bbox))
    except Exception:
        tab_active_present = False

    try:
        halo_s, halo_v, inner_s, ring_count = white_border_halo_stats(image_bgr, bbox)
    except Exception:
        halo_s = halo_v = inner_s = 0.0
        ring_count = 0
    try:
        white_border_present = bool(has_white_border_in_bbox_percent(image_bgr, bbox))
    except Exception:
        white_border_present = False

    result = {
        "red_dot_present": red_dot_present,
        "tab_active": tab_active_present,
        "mean_saturation": mean_s,
        "mean_value": mean_v,
        "white_border": white_border_present,
        "halo_saturation": halo_s,
        "halo_value": halo_v,
        "interior_saturation": inner_s,
        "ring_count": int(ring_count),
    }
    st.session_state[cache_key] = result
    return result


def _run_live_detectors(
    *,
    image_bgr: Any,
    bbox: dict[str, Any],
    has_red_dot_capability: bool,
    instance_id: str,
    region_name: str,
) -> None:
    if not isinstance(bbox, dict):
        st.info("No `bbox` on the selected region — detectors need a labeled box.")
        return

    metrics = _cached_detector_run(
        image_bgr=image_bgr,
        bbox=bbox,
        instance_id=instance_id,
        region_name=region_name,
    )
    present_rd = bool(metrics["red_dot_present"])
    if has_red_dot_capability:
        _render_detector_block(
            title="isRedDot · red-dot / frost-badge",
            matched=present_rd,
            primary="present" if present_rd else "absent",
            extras=[("has_red_dot capability", "yes")],
            caption=(
                "Detector: `layout.red_dot_detector.has_red_dot_in_bbox_percent` "
                "(accepts the frost-badge variant by default)."
            ),
        )
    else:
        _render_detector_block(
            title="isRedDot · red-dot / frost-badge",
            matched=False,
            primary="disabled",
            extras=[("has_red_dot capability", "no")],
            caption=(
                "Region has `has_red_dot: false` in area.json — overlay rules with "
                "`isRedDot:` are skipped, but the detector itself still runs below."
            ),
        )
        _render_detector_block(
            title="isRedDot · forced run (capability OFF)",
            matched=present_rd,
            primary="present" if present_rd else "absent",
            extras=[],
            caption="Forced detector run — ignores the area.json capability gate.",
        )

    mean_s = float(metrics["mean_saturation"])
    mean_v = float(metrics["mean_value"])
    active_ta = bool(metrics["tab_active"])
    _render_detector_block(
        title="isTabActive · tab-strip highlight",
        matched=active_ta,
        primary="active" if active_ta else "inactive",
        extras=[
            (
                f"Mean S (< {TAB_ACTIVE_MAX_MEAN_SATURATION:.0f})",
                f"{mean_s:.1f}",
            ),
            (
                f"Mean V (> {TAB_ACTIVE_MIN_MEAN_VALUE:.0f})",
                f"{mean_v:.1f}",
            ),
        ],
        caption=(
            "Detector: `layout.tab_active_detector.is_tab_active_in_bbox_percent` — "
            "active iff mean HSV saturation is low **and** mean value is high."
        ),
    )

    halo_s = float(metrics["halo_saturation"])
    halo_v = float(metrics["halo_value"])
    inner_s = float(metrics["interior_saturation"])
    ring_count = int(metrics["ring_count"])
    border_present = bool(metrics["white_border"])
    excess = inner_s - halo_s
    _render_detector_block(
        title="isWhiteBorder · near-white halo",
        matched=border_present,
        primary="present" if border_present else "absent",
        extras=[
            (
                f"Halo S (< {WHITE_BORDER_MAX_MEAN_SATURATION:.0f})",
                f"{halo_s:.1f}",
            ),
            (
                f"Halo V (> {WHITE_BORDER_MIN_MEAN_VALUE:.0f})",
                f"{halo_v:.1f}",
            ),
            (
                f"Inner−halo S (≥ {WHITE_BORDER_MIN_INTERIOR_SATURATION_EXCESS:.0f})",
                f"{excess:+.1f}",
            ),
            (
                f"Ring px (≥ {WHITE_BORDER_MIN_RING_PIXELS})",
                f"{ring_count}",
            ),
        ],
        caption=(
            "Detector: `layout.white_border_detector.has_white_border_in_bbox_percent` — "
            "needs near-white halo **and** a more-colored interior."
        ),
    )


def render_idle_overlay_probe(*, ctx: ClickApprovalsCtx, client: Any) -> None:
    """Inspect overlay rule metrics on the rolling PNG.

    Layout: three tabs inside the parent expander.

    * **Rule** — filters (search, current node, action types), rule selectbox,
      action-specific metric strip and reason line for the selected rule.
    * **Visualization** — selective debug-overlay toggles (search ROI, match
      box, tap marker, area bbox, detector ROIs), optional multi-region
      bbox layer, the annotated PNG, and live-vs-template crops for the
      currently-selected region.
    * **Detectors** — direct runs of ``has_red_dot_in_bbox_percent`` /
      ``is_tab_active_in_bbox_percent`` / ``has_white_border_in_bbox_percent``
      on the selected region, with the underlying HSV/halo statistics so
      threshold tuning is visible.
    """
    from .common import active_player_state_flat

    instance_id = ctx.instance_id
    state_flat = active_player_state_flat(client=client, instance_id=instance_id)
    st.caption(
        "Uses the same rolling PNG and overlay evaluation as the worker, "
        "including Redis **`current_screen`** for YAML **`screens`** rules."
    )
    analyzer_scope = _render_analyzer_scope_controls(
        ctx=ctx, client=client, instance_id=instance_id
    )
    if st.button(
        "Reload overlay scores",
        width="stretch",
        key=f"idle_overlay_probe_reload::{instance_id}",
        help=(
            "This panel does not auto-refresh; click after a new rolling PNG "
            "if scores look stale."
        ),
    ):
        clear_pipeline_overlay_cache_entries(instance_id)
        st.session_state["pipeline_force_refresh_nonce"] = force_nonce() + 1
        st.rerun()

    row = get_instance_state(client, instance_id)
    current_screen = str(row.get("current_screen") or "").strip()

    data, rebuilt = get_or_build_pipeline_cache(
        instance_id,
        repo_root=ctx.repo_root,
        area_path=ctx.area_path,
        current_screen=current_screen or None,
        module_scope=None if analyzer_scope == "disabled" else analyzer_scope,
    )
    if rebuilt:
        st.caption("Overlay recomputed on rolling PNG.")
    if data is None:
        from ui.reference_preview import rolling_live_preview_path

        preview_path = rolling_live_preview_path(instance_id)
        rel = (
            preview_path.relative_to(ctx.repo_root)
            if preview_path.is_file()
            else preview_path
        )
        st.info(
            f"No rolling preview yet: `{rel}` — start the worker or capture from **Instance**."
        )
        return

    results: dict = data["results"]
    rule_order: list[str] = data["rule_order"]
    rule_search: dict[str, str] = data["rule_search"]
    rule_node: dict[str, str] = data["rule_node"]
    area_doc: dict = data["area_doc"]
    image_bgr = data["image_bgr"]
    h, w = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    all_region_names = _area_region_names(area_doc)

    # Rule filter + selector stays at the top of the section (full width) —
    # both the left-column visualization and the right-column tabs need
    # ``sel``, so we cannot move it inside a tab. Two-column layout follows
    # once ``sel`` and the derived payload are known.
    fc1, fc2 = st.columns([1.4, 1], vertical_alignment="bottom")
    with fc1:
        name_filter = st.text_input(
            "Filter rule / region / search",
            value="",
            key=f"idle_overlay_probe_name_filter::{instance_id}",
            placeholder="e.g. hand_pointer, button.claim",
        )
    with fc2:
        only_current_node = st.checkbox(
            "Only rules for current node (+ globals)",
            value=True,
            key=f"idle_overlay_probe_only_node::{instance_id}",
            help=(
                "Hide overlay rows whose YAML `screens` gate does not match Redis "
                "`current_screen` (first listed screen only). Rows without `screens` "
                "stay visible."
            ),
        )
        st.caption(f"`current_screen`: `{current_screen or '—'}`")

    st.caption("Show overlay rules whose action is:")
    act_cols = st.columns(len(_ACTION_TYPES))
    action_visible: dict[str, bool] = {}
    for col, act_name in zip(act_cols, _ACTION_TYPES, strict=False):
        with col:
            action_visible[act_name] = st.checkbox(
                act_name,
                value=True,
                key=f"idle_overlay_probe_act::{instance_id}::{act_name}",
            )

    q = (name_filter or "").strip().lower()
    overlay_regions: set[str] = set()
    visible: list[str] = []
    for logical in rule_order:
        payload = results.get(logical)
        if not isinstance(payload, dict):
            continue
        node = str(rule_node.get(logical, "") or "").strip()
        if (
            only_current_node
            and current_screen
            and node
            and node.lower() != current_screen.lower()
        ):
            continue
        act_p = str(payload.get("action") or "").strip()
        if act_p in _ACTION_TYPES and not action_visible.get(act_p, True):
            continue
        region_name = str(payload.get("region") or "").strip()
        sr_disp = str(payload.get("search_region") or rule_search.get(logical, "") or "")
        if region_name:
            overlay_regions.add(region_name)
        if q:
            hay = " ".join([logical, region_name, sr_disp]).lower()
            if q not in hay:
                continue
        visible.append(f"overlay::{logical}")

    if q:
        for region_name in all_region_names:
            if region_name in overlay_regions:
                continue
            if q not in region_name.lower():
                continue
            visible.append(f"area::{region_name}")

    if not visible:
        st.warning("No overlay rules or area regions match the filters.")
        return

    def _fmt_rule(ln: str) -> str:
        if ln.startswith("area::"):
            reg_name = ln.removeprefix("area::")
            return f"area.json region · `{reg_name}`"
        logical = ln.removeprefix("overlay::")
        pl = results.get(logical)
        reg = str(pl.get("region") or "") if isinstance(pl, dict) else ""
        act_disp = str(pl.get("action") or "") if isinstance(pl, dict) else ""
        suf = f" · `{reg}`" if reg else ""
        if act_disp:
            suf += f" · _{act_disp}_"
        return f"{logical}{suf}"

    sel = st.selectbox(
        "Overlay rule / area region",
        visible,
        key=f"idle_overlay_probe_rule::{instance_id}",
        format_func=_fmt_rule,
    )

    is_area_region = sel.startswith("area::")
    sel_logical = sel.removeprefix("overlay::")
    if is_area_region:
        area_region = sel.removeprefix("area::")
        pair0 = screen_region_by_name(area_doc, area_region, state_flat=state_flat)
        if pair0 is None:
            st.error("Missing area region payload.")
            return
        entry0, reg0 = pair0
        pay = {
            "region": area_region,
            "action": str(reg0.get("action") or "").strip(),
            "matched": False,
            "threshold": reg0.get("threshold"),
            "_area_ref": str(entry0.get("ocr") or "").strip(),
            "_area_type": str(reg0.get("type") or "").strip(),
        }
        if pay["action"] == "text":
            _probe_area_region_ocr(
                pay=pay,
                image_bgr=image_bgr,
                reg=reg0,
                instance_id=instance_id,
            )
        elif pay["action"] == "exist":
            _probe_area_region_exist(
                pay=pay,
                image_bgr=image_bgr,
                area_doc=area_doc,
                repo_root=ctx.repo_root,
                region_name=area_region,
                state_flat=state_flat,
                instance_id=instance_id,
                current_screen=current_screen or None,
            )
    else:
        pay = results.get(sel_logical)
    if not isinstance(pay, dict):
        st.error("Missing overlay payload for this rule.")
        return

    act = str(pay.get("action") or "").strip()
    nd = "" if is_area_region else str(rule_node.get(sel_logical, "") or "").strip()

    st.divider()
    # Two-column layout: visualization on the left, rule metadata + detectors
    # on the right (as a 2-tab strip).
    col_viz, col_meta = st.columns([1, 1], gap="large")

    reg_name = str(pay.get("region") or "").strip()
    selected_pair = (
        screen_region_by_name(area_doc, reg_name, state_flat=state_flat)
        if reg_name
        else None
    )
    selected_entry = selected_pair[0] if selected_pair else None
    selected_reg = selected_pair[1] if selected_pair else None
    selected_bbox = (
        selected_reg.get("bbox")
        if isinstance(selected_reg, dict) and isinstance(selected_reg.get("bbox"), dict)
        else None
    )

    with col_viz:
        st.caption("Toggle which debug layers to draw on the rolling PNG.")
        dl1, dl2, dl3, dl4 = st.columns(4)
        with dl1:
            show_search_roi = st.checkbox(
                "Search ROI (orange)",
                value=True,
                key=f"idle_overlay_probe_show_search_roi::{instance_id}",
                help="Search region used to find this rule's template.",
            )
        with dl2:
            show_match_box = st.checkbox(
                "Match box (green/cyan)",
                value=True,
                key=f"idle_overlay_probe_show_match_box::{instance_id}",
                help="Template match — green when matched, cyan when rejected.",
            )
        with dl3:
            show_tap = st.checkbox(
                "Tap target (red cross)",
                value=True,
                key=f"idle_overlay_probe_show_tap::{instance_id}",
            )
        with dl4:
            show_area_bbox = st.checkbox(
                "area.json bbox (orange)",
                value=is_area_region,
                key=f"idle_overlay_probe_show_area_bbox::{instance_id}",
                help="Source bbox of the rule's region from area.json.",
            )

        st.caption(
            "Overlay extra detector ROIs on the selected region — see the "
            "**Detectors** tab for the live result for each."
        )
        dd1, dd2, dd3 = st.columns(3)
        with dd1:
            show_red_dot_roi = st.checkbox(
                "red_dot ROI (red)",
                value=False,
                key=f"idle_overlay_probe_show_red_dot::{instance_id}",
            )
        with dd2:
            show_tab_active_roi = st.checkbox(
                "tab_active ROI (green)",
                value=False,
                key=f"idle_overlay_probe_show_tab_active::{instance_id}",
            )
        with dd3:
            show_white_border_roi = st.checkbox(
                "white_border ROI (white)",
                value=False,
                key=f"idle_overlay_probe_show_white_border::{instance_id}",
            )

        extra_regions_pick: list[str] = st.multiselect(
            "Also draw these area.json region bboxes",
            options=all_region_names,
            default=[],
            key=f"idle_overlay_probe_extra_regions::{instance_id}",
            help=(
                "Highlight additional region bboxes from area.json on the debug "
                "image. Useful to compare neighboring regions or confirm coverage."
            ),
        )

        extra_region_bboxes: list[tuple[str, dict[str, Any]]] = []
        for reg_pick in extra_regions_pick:
            pair_pick = screen_region_by_name(area_doc, reg_pick, state_flat=state_flat)
            if pair_pick is None:
                continue
            bb_pick = pair_pick[1].get("bbox")
            if isinstance(bb_pick, dict):
                extra_region_bboxes.append((reg_pick, bb_pick))

        detector_bboxes: list[tuple[str, dict[str, Any], tuple[int, int, int]]] = []
        if selected_bbox is not None:
            if show_red_dot_roi:
                detector_bboxes.append(
                    (f"red_dot · {reg_name}", selected_bbox, detector_color("red_dot"))
                )
            if show_tab_active_roi:
                detector_bboxes.append(
                    (
                        f"tab_active · {reg_name}",
                        selected_bbox,
                        detector_color("tab_active"),
                    )
                )
            if show_white_border_roi:
                detector_bboxes.append(
                    (
                        f"white_border · {reg_name}",
                        selected_bbox,
                        detector_color("white_border"),
                    )
                )

        try:
            vis = annotate_overlay_layers(
                image_bgr,
                results=results,
                logical_names=[] if is_area_region else [sel_logical],
                area_doc=area_doc,
                rule_search=rule_search,
                show_search_roi=show_search_roi and not is_area_region,
                show_match_box=show_match_box and not is_area_region,
                show_tap=show_tap and not is_area_region,
                show_area_bbox=show_area_bbox or is_area_region,
                extra_region_bboxes=extra_region_bboxes,
                detector_bboxes=detector_bboxes,
            )
            # area.json picks have no overlay payload; force-draw their bbox so
            # the user always sees what they selected even with "area bbox" off.
            if is_area_region and selected_bbox is not None and not show_area_bbox:
                draw_bbox_pct(
                    vis,
                    selected_bbox,
                    color=(0, 165, 255),
                    thickness=3,
                    label=reg_name,
                )
            vis_ui = maybe_downscale_for_ui(vis, max_side=ctx.probe_overlay_max_side)
            ok_vis, enc_vis = cv2.imencode(".png", vis_ui)
            if ok_vis:
                dbg_png = enc_vis.tobytes()
                fitted_dbg, _native_dbg, _ = png_bytes_fitted(
                    dbg_png, ctx.probe_overlay_max_side
                )
                legend_bits: list[str] = []
                if not is_area_region:
                    if show_search_roi:
                        legend_bits.append("**orange** = `search_region` ROI")
                    if show_match_box:
                        legend_bits.append(
                            "**green** / **cyan** = match box (matched / rejected)"
                        )
                    if show_tap:
                        legend_bits.append("**red cross** = tap target")
                if show_area_bbox or is_area_region:
                    legend_bits.append("**orange** = area.json bbox")
                if extra_region_bboxes:
                    legend_bits.append("**blue** = extra area regions")
                if detector_bboxes:
                    legend_bits.append("**detector ROIs** colored per checkbox")
                cap = " · ".join(legend_bits) if legend_bits else (
                    "All debug layers are off — enable a toggle above."
                )
                if act == "color_check" and not is_area_region:
                    cap += " · (`color_check` has no match box / tap marker)"
                st.image(fitted_dbg, caption=cap, width="stretch")
        except Exception:
            st.caption("Could not draw overlay debug on screenshot.")

        if not reg_name:
            st.caption("Selected rule has no `region` — skipping live vs template crops.")
        elif selected_pair is None or selected_bbox is None:
            st.caption(f"No `{reg_name}` bbox in area.json — skipping live vs template crops.")
        else:
            resolved_region = (
                str(selected_reg.get("name") or "").strip() if selected_reg else ""
            ) or reg_name
            ref_rel = (
                effective_ocr_for_region(selected_entry, selected_reg)
                if selected_entry and selected_reg
                else ""
            )
            if ref_rel:
                _render_live_vs_template_crops(
                    ctx=ctx,
                    image_bgr=image_bgr,
                    bbox=selected_bbox,
                    reg_name=reg_name,
                    resolved_region=resolved_region,
                    ref_rel=ref_rel,
                    w=w,
                    h=h,
                )

    with col_meta:
        tab_info, tab_detectors = st.tabs(["Rule info", "Detectors"])
        with tab_info:
            _render_rule_metrics(act=act, pay=pay, instance_id=instance_id, sel=sel)
            _render_rule_info_line(
                pay=pay,
                rule_search_name=rule_search.get(sel_logical, ""),
                sel_logical=sel_logical,
                is_area_region=is_area_region,
                act=act,
                nd=nd,
            )
        with tab_detectors:
            if not reg_name:
                st.info("Select a rule that has a `region` to run detectors.")
            elif selected_bbox is None:
                st.info(
                    f"No `{reg_name}` bbox in area.json — detectors need a labeled bbox."
                )
            else:
                cap_bits: list[str] = []
                if reg_name:
                    cap_bits.append(f"region `{reg_name}`")
                if selected_bbox is not None:
                    bbx = float(selected_bbox.get("x") or 0.0)
                    bby = float(selected_bbox.get("y") or 0.0)
                    bbw = float(selected_bbox.get("width") or 0.0)
                    bbh = float(selected_bbox.get("height") or 0.0)
                    cap_bits.append(
                        f"bbox `{bbx:.2f}%, {bby:.2f}% · {bbw:.2f}×{bbh:.2f}%`"
                    )
                st.caption(" · ".join(cap_bits))
                has_rd_cap = bool(
                    isinstance(selected_reg, dict) and selected_reg.get("has_red_dot")
                )
                _run_live_detectors(
                    image_bgr=image_bgr,
                    bbox=selected_bbox,
                    has_red_dot_capability=has_rd_cap,
                    instance_id=instance_id,
                    region_name=reg_name,
                )


def _render_live_vs_template_crops(
    *,
    ctx: ClickApprovalsCtx,
    image_bgr: Any,
    bbox: dict[str, Any],
    reg_name: str,
    resolved_region: str,
    ref_rel: str,
    w: int,
    h: int,
) -> None:
    L, T, R, B = _pct_bbox_to_px_rect(bbox, w, h)
    pad = 6
    L = max(0, min(w - 1, int(L - pad)))
    T = max(0, min(h - 1, int(T - pad)))
    R = max(L + 1, min(w, int(R + pad)))
    B = max(T + 1, min(h, int(B + pad)))

    found_png: bytes | None = None
    try:
        frag = image_bgr[T:B, L:R].copy()
        ok2, enc2 = cv2.imencode(".png", frag)
        if ok2:
            found_png = enc2.tobytes()
    except Exception:
        found_png = None

    sought_png: bytes | None = None
    sought_name: str | None = None
    try:
        area_mtime = float(ctx.area_path.stat().st_mtime) if ctx.area_path.is_file() else 0.0
        crop_path = exported_crop_png(ctx.repo_root, ref_rel, resolved_region)
        _ensure_fresh_reference_crop(
            repo_root=ctx.repo_root,
            ref_rel=ref_rel,
            region_name=resolved_region,
            bbox_pct=bbox,
            crop_path=crop_path,
            area_mtime=area_mtime,
        )
        if crop_path.is_file():
            tpl = cv2.imread(str(crop_path))
            if tpl is not None:
                ok3, enc3 = cv2.imencode(".png", tpl)
                if ok3:
                    sought_png = enc3.tobytes()
                    sought_name = crop_path.name
    except Exception:
        sought_png = None

    cap_max = ctx.region_crop_max_side
    with st.container(border=True):
        st.markdown(f"**`{reg_name}`** — live crop vs template (`references/crop/`)")
        lbl_ref = labeling_query_ref_from_area_ocr(ref_rel)
        if lbl_ref:
            st.page_link(
                "views/labeling.py",
                label=f"Open `{lbl_ref}` in Labeling (region `{reg_name}`)",
                query_params={"ref": lbl_ref},
                width="stretch",
            )

        c_found, c_sought = st.columns(2, gap="medium", vertical_alignment="top")
        with c_found:
            st.caption("Live (rolling PNG)")
            if found_png is not None:
                fitted2, native2, _ = png_bytes_fitted(found_png, cap_max)
                st.image(fitted2, caption=f"{native2[0]}×{native2[1]} px", width="stretch")
            else:
                st.caption("—")
        with c_sought:
            st.caption("Template crop")
            if sought_png is not None:
                fitted3, native3, _ = png_bytes_fitted(sought_png, cap_max)
                st.image(
                    fitted3,
                    caption=f"{sought_name or reg_name} · {native3[0]}×{native3[1]} px",
                    width="stretch",
                )
            else:
                st.caption("—")
