from __future__ import annotations

from typing import Any

import cv2
import streamlit as st

from layout.area_lookup import screen_region_by_name
from layout.crop_paths import exported_crop_png
from ui.pipeline.data import (
    clear_pipeline_overlay_cache_entries,
    force_nonce,
    get_or_build_pipeline_cache,
)
from ui.pipeline.overlay_viz import annotate_overlay_debug, maybe_downscale_for_ui
from ui.preview_display import png_bytes_fitted
from ui.redis_client import get_instance_state

from .common import labeling_query_ref_from_area_ocr
from .ctx import ClickApprovalsCtx


def _pct_bbox_to_px_rect(bb: dict[str, object], w: int, h: int) -> tuple[int, int, int, int]:
    x = float(bb.get("x") or 0.0)
    y = float(bb.get("y") or 0.0)
    bw = float(bb.get("width") or 0.0)
    bh = float(bb.get("height") or 0.0)
    left = max(0, min(w - 1, int(x / 100.0 * w)))
    top = max(0, min(h - 1, int(y / 100.0 * h)))
    right = max(left + 1, min(w, int((x + bw) / 100.0 * w)))
    bottom = max(top + 1, min(h, int((y + bh) / 100.0 * h)))
    return left, top, right, bottom


def _ensure_fresh_reference_crop(
    *,
    repo_root: Any,
    ref_rel: str,
    region_name: str,
    bbox_pct: dict[str, object],
    crop_path: Any,
    area_mtime: float,
) -> None:
    """Ensure `references/crop/...` exists and matches latest bbox.

    Re-exports when crop is missing or older than area.json / reference PNG.
    """
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


def render_idle_overlay_probe(*, ctx: ClickApprovalsCtx, client: Any) -> None:
    """Inspect overlay rule metrics on the rolling PNG."""
    instance_id = ctx.instance_id
    st.caption(
        "Uses the same rolling PNG and overlay evaluation as the worker, "
        "including Redis **`current_screen`** for YAML **`node`** rules."
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

    fc1, fc2 = st.columns([1.4, 1], vertical_alignment="bottom")
    with fc1:
        name_filter = st.text_input(
            "Filter rule / region / search",
            value="",
            key=f"idle_overlay_probe_name_filter::{instance_id}",
            placeholder="e.g. hand_pointer, claim_button",
        )
    with fc2:
        only_current_node = st.checkbox(
            "Only rules for current node (+ globals)",
            value=True,
            key=f"idle_overlay_probe_only_node::{instance_id}",
            help=(
                "Hide overlay rows whose YAML `node` differs from Redis `current_screen`. "
                "Rows without `node` always stay visible."
            ),
        )
        st.caption(f"`current_screen`: `{current_screen or '—'}`")

    data, rebuilt = get_or_build_pipeline_cache(
        instance_id,
        repo_root=ctx.repo_root,
        area_path=ctx.area_path,
        analyze_path=ctx.analyze_path,
        current_screen=current_screen or None,
    )
    if rebuilt:
        st.caption("Overlay recomputed on rolling PNG.")
    if data is None:
        from ui.reference_preview import rolling_live_preview_path

        preview_path = rolling_live_preview_path(instance_id)
        rel = preview_path.relative_to(ctx.repo_root) if preview_path.is_file() else preview_path
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

    q = (name_filter or "").strip().lower()
    visible: list[str] = []
    for logical in rule_order:
        payload = results.get(logical)
        if not isinstance(payload, dict):
            continue
        node = str(rule_node.get(logical, "") or "").strip()
        if only_current_node and current_screen and node and node != current_screen:
            continue
        region_name = str(payload.get("region") or "").strip()
        sr_disp = str(payload.get("search_region") or rule_search.get(logical, "") or "")
        if q:
            hay = " ".join([logical, region_name, sr_disp]).lower()
            if q not in hay:
                continue
        visible.append(logical)

    if not visible:
        st.warning("No overlay rules match the filters.")
        return

    def _fmt_rule(ln: str) -> str:
        pl = results.get(ln)
        reg = str(pl.get("region") or "") if isinstance(pl, dict) else ""
        suf = f" · `{reg}`" if reg else ""
        return f"{ln}{suf}"

    sel = st.selectbox(
        "Overlay rule (reference / labeling region)",
        visible,
        key=f"idle_overlay_probe_rule::{instance_id}",
        format_func=_fmt_rule,
    )

    pay = results.get(sel)
    if not isinstance(pay, dict):
        st.error("Missing overlay payload for this rule.")
        return

    act = str(pay.get("action") or "").strip()
    matched = bool(pay.get("matched"))
    nd = str(rule_node.get(sel, "") or "").strip()

    if act == "color_check":
        want = str(pay.get("want") or "").strip().lower()
        dom = str(pay.get("dominant") or "").strip().lower()
        share_raw = pay.get("share")
        thr_raw = pay.get("threshold")
        share_f: float | None = None
        thr_f: float | None = None
        try:
            if share_raw is not None and str(share_raw).strip() != "":
                share_f = float(share_raw)
        except (TypeError, ValueError):
            share_f = None
        try:
            if thr_raw is not None and str(thr_raw).strip() != "":
                thr_f = float(thr_raw)
        except (TypeError, ValueError):
            thr_f = None
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
                  <div style="font-size: 0.85rem; opacity: 0.75;">dominant == want &amp; share ≥ threshold</div>
                  <div style="font-size: 1.75rem; font-weight: 650; line-height: 1.2; color: {color};">{txt}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with m2:
            st.metric("Dominant", dom or "—")
        with m3:
            st.metric("Want", want or "—")
        with m4:
            st.metric("Share / threshold", f"{share_f:.3f} / {thr_f:.3f}" if ok_eval else "—")
    else:
        score_raw = pay.get("score")
        thr_raw = pay.get("threshold")
        score_f: float | None = None
        thr_f: float | None = None
        try:
            if score_raw is not None and str(score_raw).strip() != "":
                score_f = float(score_raw)
        except (TypeError, ValueError):
            score_f = None
        try:
            if thr_raw is not None and str(thr_raw).strip() != "":
                thr_f = float(thr_raw)
        except (TypeError, ValueError):
            thr_f = None
        gap_s = None
        if score_f is not None and thr_f is not None:
            gap_s = f"{score_f - thr_f:+.4f}"

        m1, m2, m3, m4 = st.columns(4)
        with m1:
            ok_eval = score_f is not None and thr_f is not None
            if not ok_eval:
                txt = "—"
                color = "#9aa0a6"
            else:
                txt = "yes" if matched else "no"
                color = "#16a34a" if matched else "#dc2626"
            st.markdown(
                f"""
                <div>
                  <div style="font-size: 0.85rem; opacity: 0.75;">≥ threshold</div>
                  <div style="font-size: 1.75rem; font-weight: 650; line-height: 1.2; color: {color};">{txt}</div>
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

    sr_line = str(pay.get("search_region") or rule_search.get(sel, "") or "").strip()
    st.markdown(
        f"**Region:** `{str(pay.get('region') or '').strip() or '—'}` · "
        f"**action:** `{act or '—'}` · "
        f"**YAML node:** `{nd or '(global)'}` · "
        f"**search_region:** `{sr_line or '—'}`"
    )
    reason = str(pay.get("reason") or "").strip()
    detail = str(pay.get("detail") or "").strip()
    bits = [reason] if reason else []
    if detail and detail != reason:
        bits.append(detail)
    if bits:
        st.caption(" · ".join(bits))

    reg_name = str(pay.get("region") or "").strip()
    if not reg_name:
        return

    pair = screen_region_by_name(area_doc, reg_name)
    if pair is None or not isinstance(pair[1].get("bbox"), dict):
        st.caption(f"No `{reg_name}` bbox in area.json — skipping live vs template crops.")
        return
    entry, reg = pair
    ref_rel = str(entry.get("ocr") or "").strip()
    if not ref_rel:
        return

    L, T, R, B = _pct_bbox_to_px_rect(reg["bbox"], w, h)
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
        crop_path = exported_crop_png(ctx.repo_root, ref_rel, reg_name)
        _ensure_fresh_reference_crop(
            repo_root=ctx.repo_root,
            ref_rel=ref_rel,
            region_name=reg_name,
            bbox_pct=reg["bbox"],
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
        draw_dbg = st.checkbox(
            "Draw search ROI / match box / tap on rolling PNG",
            value=True,
            key=f"idle_overlay_probe_draw_regions::{instance_id}",
            help="Same overlay debug colors (search ROI, match box, tap).",
        )
        if draw_dbg:
            try:
                vis = annotate_overlay_debug(
                    image_bgr,
                    results,
                    [sel],
                    area_doc,
                    rule_search,
                )
                vis_ui = maybe_downscale_for_ui(vis, max_side=ctx.probe_overlay_max_side)
                ok_vis, enc_vis = cv2.imencode(".png", vis_ui)
                if ok_vis:
                    dbg_png = enc_vis.tobytes()
                    fitted_dbg, _native_dbg, _ = png_bytes_fitted(
                        dbg_png, ctx.probe_overlay_max_side
                    )
                    cap = (
                        "**Orange** outline: `search_region` ROI · "
                        "**Green** / **cyan**: template match (green = ≥ threshold) · "
                        "**Red** cross: tap target"
                    )
                    if str(pay.get("action") or "").strip() == "color_check":
                        cap = (
                            "**Orange** outline: `search_region` ROI (if any) · "
                            "(`color_check` has no match box / tap marker)"
                        )
                    st.image(fitted_dbg, caption=cap, width="stretch")
            except Exception:
                st.caption("Could not draw overlay debug on screenshot.")

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
