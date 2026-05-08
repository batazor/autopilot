"""Approval page: when open, sensitive bot actions require explicit approval.

Contract:
- Page open => refreshes Redis heartbeat key (per instance).
- Worker inputs (ADB tap/swipe/type, DSL ``set_node``):
  - Approval gating defaults **ON** when ``enabled`` key is unset (turn OFF in the toggle below).
  - If no heartbeat => block
  - If heartbeat present => publish single "current" request and wait for approve/reject
  - After Approve/Reject, UI writes ``response_key`` then deletes ``current`` so the preview clears;
    worker polls ``response_key`` first so this does not cancel an approve.
"""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

import cv2
import httpx
import numpy as np
import streamlit as st

from actions.tap import click_approval_enabled
from analysis.overlay_manifest import default_analyze_yaml_path
from config.loader import load_settings
from layout.area_lookup import screen_region_by_name
from layout.crop_paths import exported_crop_png
from ui.pipeline.data import (
    clear_pipeline_overlay_cache_entries,
    force_nonce,
    get_or_build_pipeline_cache,
)
from ui.pipeline.overlay_viz import annotate_overlay_debug, maybe_downscale_for_ui
from ui.notifications import pop_new_notifications
from ui.preview_display import png_bytes_fitted
from ui.redis_client import get_instance_state, require_redis_connection
from ui.reference_preview import load_rolling_instance_preview

settings = load_settings()
client = require_redis_connection()

_REPO = Path(__file__).resolve().parents[2]
_AREA = _REPO / "area.json"
_ANALYZE = default_analyze_yaml_path(_REPO)

inst_ids = [i.instance_id for i in settings.instances]
if not inst_ids:
    st.info("No instances in config.")
    st.stop()

instance_id = st.selectbox("Instance", inst_ids, key="click_approval_instance")

hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
current_key = f"wos:ui:click_approval:current:{instance_id}"

_PREVIEW_MAX_SIDE = 360
# Idle overlay probe: full-frame debug fit (search ROI / match / tap).
_PROBE_OVERLAY_MAX_SIDE = 900
# Thumbnails below main shot: keep each column narrower so the pair does not overflow.
_REGION_CROP_MAX_SIDE = 220


@st.cache_data(ttl=5)
def _ocr_health_status() -> tuple[bool, str]:
    url = str(getattr(settings, "ocr", None).url if getattr(settings, "ocr", None) else "").strip()
    if not url:
        return False, "OCR url is not configured"
    try:
        with httpx.Client(timeout=1.0) as c:
            r = c.get(f"{url}/health")
            r.raise_for_status()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 - UI diagnostic only
        return False, f"{type(exc).__name__}: {exc}"


@st.cache_data(ttl=60)
def _load_area_doc_cached(_mtime: float) -> dict[str, object]:
    """Cache the parsed ``area.json`` keyed by file mtime.

    Passing ``mtime`` makes the cache self-invalidate the moment the file is
    edited (e.g. via the area annotator) — without paying for a JSON parse on
    every fragment rerun while the file is unchanged.
    """
    if not _AREA.is_file():
        return {}
    try:
        return json.loads(_AREA.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_area_doc() -> dict[str, object]:
    try:
        mtime = _AREA.stat().st_mtime if _AREA.is_file() else 0.0
    except OSError:
        mtime = 0.0
    return _load_area_doc_cached(mtime)


def _labeling_query_ref_from_area_ocr(ocr_rel: str) -> str | None:
    """Path under ``references/`` for Labeling ``?ref=`` (same convention as Gallery / Wiki)."""
    s = (ocr_rel or "").replace("\\", "/").strip().lstrip("/")
    if not s:
        return None
    if s.startswith("references/"):
        s = s.removeprefix("references/")
    return s or None


def _render_idle_overlay_threshold_probe(instance_id: str) -> None:
    """When no tap approval is pending: inspect findIcon score vs threshold on the rolling frame."""
    st.caption(
        "Uses the same rolling PNG and overlay evaluation as the worker, "
        "including Redis **`current_screen`** for YAML **`node`** rules."
    )
    if st.button(
        "Reload overlay scores",
        width="stretch",
        key=f"idle_overlay_probe_reload::{instance_id}",
        help="This panel does not auto-refresh; click after a new rolling PNG if scores look stale.",
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
        repo_root=_REPO,
        area_path=_AREA,
        analyze_path=_ANALYZE,
        current_screen=current_screen or None,
    )
    if rebuilt:
        st.caption("Overlay recomputed on rolling PNG.")
    if data is None:
        from ui.reference_preview import rolling_live_preview_path

        preview_path = rolling_live_preview_path(instance_id)
        rel = preview_path.relative_to(_REPO) if preview_path.is_file() else preview_path
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

    matched = bool(pay.get("matched"))
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

    nd = str(rule_node.get(sel, "") or "").strip()

    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("≥ threshold", "yes" if matched else "no")
    with m2:
        st.metric("Score", f"{score_f:.4f}" if score_f is not None else "—")
    with m3:
        st.metric("Threshold", f"{thr_f:.4f}" if thr_f is not None else "—")
    with m4:
        st.metric("Score − thr", gap_s if gap_s is not None else "—")

    sr_line = str(pay.get("search_region") or rule_search.get(sel, "") or "").strip()
    st.markdown(
        f"**Region:** `{str(pay.get('region') or '').strip() or '—'}` · "
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
        crop_path = exported_crop_png(_REPO, ref_rel, reg_name)
        if crop_path.is_file():
            tpl = cv2.imread(str(crop_path))
            if tpl is not None:
                ok3, enc3 = cv2.imencode(".png", tpl)
                if ok3:
                    sought_png = enc3.tobytes()
                    sought_name = crop_path.name
    except Exception:
        sought_png = None

    cap_max = _REGION_CROP_MAX_SIDE
    with st.container(border=True):
        st.markdown(f"**`{reg_name}`** — live crop vs template (`references/crop/`)")
        lbl_ref = _labeling_query_ref_from_area_ocr(ref_rel)
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
                vis_ui = maybe_downscale_for_ui(vis, max_side=_PROBE_OVERLAY_MAX_SIDE)
                ok_vis, enc_vis = cv2.imencode(".png", vis_ui)
                if ok_vis:
                    dbg_png = enc_vis.tobytes()
                    fitted_dbg, native_dbg, _ = png_bytes_fitted(dbg_png, _PROBE_OVERLAY_MAX_SIDE)
                    st.image(
                        fitted_dbg,
                        caption=(
                            "**Orange** outline: `search_region` ROI · "
                            "**Green** / **cyan**: template match (green = ≥ threshold) · "
                            "**Red** cross: tap target"
                        ),
                        width="stretch",
                    )
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


def _active_player_in_game_id(inst: str) -> str:
    """OCR'd in-game ``player_id`` of the active bot account on ``inst``.

    Falls back to ``—`` when ``active_player`` is unset, points at a missing
    hash, or :ref:`who_i_am <scenarios/onboarding/who_i_am>` has not run yet.
    """
    row = get_instance_state(client, inst) or {}
    active = (row.get("active_player") or "").strip()
    if not active:
        return "—"
    try:
        raw = client.hget(f"wos:player:{active}:state", "player_id")
    except Exception:
        return "—"
    if raw is None:
        return "—"
    val = raw.decode() if isinstance(raw, bytes) else str(raw)
    return val.strip() or "—"


@st.fragment(run_every=timedelta(seconds=1))
def _header() -> None:
    row = get_instance_state(client, instance_id)
    node = (row.get("current_screen") or "").strip() or "—"
    pid_in_game = _active_player_in_game_id(instance_id)
    st.title(f"Click approvals · {instance_id}")
    st.caption(f"node: `{node}` · player_id: `{pid_in_game}`")
    ok, detail = _ocr_health_status()
    if not ok:
        ocr_url = str(settings.ocr.url).strip()
        st.warning(
            f"OCR service is not available ({ocr_url}). "
            f"Please start OCR (e.g. docker-compose) — otherwise screen OCR/detection may stall. "
            f"Details: {detail}"
        )


def _render_preview_with_point(
    *,
    instance_id: str,
    x: int | None,
    y: int | None,
    payload: dict[str, object] | None = None,
    where: object | None = None,
) -> None:
    ui = where if where is not None else st
    png, rel, mtime = load_rolling_instance_preview(instance_id)
    if png is None:
        ui.info(f"No rolling preview yet for `{instance_id}`.")
        return

    # Decode PNG to draw crosshair in pixel coords.
    arr = np.frombuffer(png, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        ui.warning("Could not decode rolling PNG.")
        return

    h, w = int(bgr.shape[0]), int(bgr.shape[1])

    def _draw_focus_rect(
        img: np.ndarray,
        *,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        label: str | None = None,
    ) -> None:
        """High-contrast box for variable backgrounds."""
        x0 = max(0, min(w - 1, int(x0)))
        y0 = max(0, min(h - 1, int(y0)))
        x1 = max(x0 + 1, min(w, int(x1)))
        y1 = max(y0 + 1, min(h, int(y1)))

        # Semi-transparent fill so it stands out even on yellow-ish UI.
        overlay = img.copy()
        cv2.rectangle(overlay, (x0, y0), (x1, y1), (0, 220, 255), -1)
        cv2.addWeighted(overlay, 0.12, img, 0.88, 0.0, img)

        # Slimmer outline (thick strokes looked messy after downscale).
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), 2, lineType=cv2.LINE_AA)
        cv2.rectangle(img, (x0, y0), (x1, y1), (255, 255, 255), 1, lineType=cv2.LINE_AA)
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 220, 255), 1, lineType=cv2.LINE_AA)

        if label:
            font = cv2.FONT_HERSHEY_SIMPLEX
            raw_t = str(label).strip()
            if not raw_t:
                return
            text = raw_t if len(raw_t) <= 42 else raw_t[:39] + "…"
            font_scale = 0.45
            thickness = 1
            (tw, th), base = cv2.getTextSize(text, font, font_scale, thickness)
            pad = 3
            gap = 5
            lab_h = th + base + pad * 2
            place_above = y0 >= lab_h + gap
            if place_above:
                by1 = y0 - gap
                by0 = by1 - lab_h
            else:
                by0 = y1 + gap
                by1 = by0 + lab_h
            bx0 = int(x0)
            bx1 = bx0 + tw + pad * 2
            if bx1 > w:
                bx0 = max(0, w - (tw + pad * 2))
                bx1 = w
            by0 = max(0, by0)
            by1 = min(h, by1)
            if by1 - by0 < lab_h:
                by0 = max(0, by1 - lab_h)
            cv2.rectangle(img, (bx0, by0), (bx1, by1), (0, 0, 0), -1, lineType=cv2.LINE_AA)
            cv2.rectangle(img, (bx0, by0), (bx1, by1), (0, 220, 255), 1, lineType=cv2.LINE_AA)
            cv2.putText(
                img,
                text,
                (bx0 + pad, by1 - pad - base),
                font,
                font_scale,
                (255, 255, 255),
                thickness,
                cv2.LINE_AA,
            )

    # Draw the zone we "found" (when available) + its confidence/score.
    # - For `overlay_tap`, we resolve `current_task_region` from context via `area.json`.
    # - `set_node` only updates the FSM `current_screen`; it does not tap any region,
    #   so we must NOT draw stale ``current_task_region`` overlays for it (those leak
    #   from the previous step's task-level Redis context and confuse the operator).
    ptype = str(payload.get("type") or "").strip().lower() if isinstance(payload, dict) else ""
    is_set_node = ptype == "set_node"
    if isinstance(payload, dict) and not is_set_node:
        if ptype == "swipe":
            try:
                x1 = int(payload.get("x1") or 0)
                y1 = int(payload.get("y1") or 0)
                x2 = int(payload.get("x2") or 0)
                y2 = int(payload.get("y2") or 0)
                ms = int(payload.get("ms") or 0)

                x1 = int(max(0, min(w - 1, x1)))
                y1 = int(max(0, min(h - 1, y1)))
                x2 = int(max(0, min(w - 1, x2)))
                y2 = int(max(0, min(h - 1, y2)))

                cv2.arrowedLine(bgr, (x1, y1), (x2, y2), (0, 0, 0), 6, tipLength=0.25)
                cv2.arrowedLine(bgr, (x1, y1), (x2, y2), (0, 220, 255), 3, tipLength=0.25)

                dist = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
                label = f"swipe {dist:.0f}px"
                if ms > 0:
                    label += f" · {ms}ms"
                _draw_focus_rect(bgr, x0=x1, y0=y1, x1=x1 + 2, y1=y1 + 2, label=label)
            except Exception:
                pass

        reg = payload.get("region")
        if isinstance(reg, dict):
            try:
                rx = int(float(reg.get("x") or 0))
                ry = int(float(reg.get("y") or 0))
                rw = int(float(reg.get("w") or 0))
                rh = int(float(reg.get("h") or 0))
                if rw > 0 and rh > 0:
                    x0 = max(0, min(w - 1, rx))
                    y0 = max(0, min(h - 1, ry))
                    x1 = max(x0 + 1, min(w, rx + rw))
                    y1 = max(y0 + 1, min(h, ry + rh))
                    _draw_focus_rect(bgr, x0=x0, y0=y0, x1=x1, y1=y1)
            except Exception:
                pass

        ctx = payload.get("context")
        if isinstance(ctx, dict):
            reg_name = str(ctx.get("current_task_region") or "").strip()
            if reg_name:
                area_doc = _load_area_doc()
                pair = screen_region_by_name(area_doc, reg_name)
                if pair is not None and isinstance(pair[1].get("bbox"), dict):
                    L, T, R, B = _pct_bbox_to_px_rect(pair[1]["bbox"], w, h)
                    _draw_focus_rect(bgr, x0=L, y0=T, x1=R, y1=B, label=reg_name)
    if x is not None and y is not None:
        px = int(max(0, min(w - 1, x)))
        py = int(max(0, min(h - 1, y)))
        # Crosshair + dot
        cv2.circle(bgr, (px, py), 10, (0, 0, 255), 2)
        cv2.circle(bgr, (px, py), 3, (0, 0, 255), -1)
        cv2.line(bgr, (px - 18, py), (px + 18, py), (0, 0, 255), 1)
        cv2.line(bgr, (px, py - 18), (px, py + 18), (0, 0, 255), 1)

    ok, enc = cv2.imencode(".png", bgr)
    if not ok:
        ui.warning("Could not encode preview image.")
        return
    out_png = enc.tobytes()
    fitted, native, _disp = png_bytes_fitted(out_png, _PREVIEW_MAX_SIDE)
    cap = f"{rel or instance_id} · {native[0]}×{native[1]}"
    if mtime is not None:
        cap = f"{cap} · {time.strftime('%H:%M:%S', time.localtime(mtime))}"
    if x is not None and y is not None:
        cap = f"{cap} · target=({x},{y})"
    ui.image(fitted, caption=cap, width="stretch")

    # Show "found" (live crop) vs "sought" (template) for overlay_tap contexts.
    # Skip for ``set_node`` — that step does not tap, so the task-level
    # ``current_task_region`` from Redis (e.g. left over from the previous step)
    # is irrelevant and would render a misleading region preview.
    if not isinstance(payload, dict) or is_set_node:
        return
    ctx = payload.get("context")
    if not isinstance(ctx, dict):
        return

    reg_name = str(ctx.get("current_task_region") or "").strip()
    if not reg_name:
        return

    area_doc = _load_area_doc()
    pair = screen_region_by_name(area_doc, reg_name)
    if pair is None:
        return
    entry, reg = pair
    if not isinstance(reg.get("bbox"), dict):
        return
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
        frag = bgr[T:B, L:R].copy()
        ok2, enc2 = cv2.imencode(".png", frag)
        if ok2:
            found_png = enc2.tobytes()
    except Exception:
        found_png = None

    sought_png: bytes | None = None
    sought_name: str | None = None
    try:
        crop_path = exported_crop_png(_REPO, ref_rel, reg_name)
        if crop_path.is_file():
            tpl = cv2.imread(str(crop_path))
            if tpl is not None:
                ok3, enc3 = cv2.imencode(".png", tpl)
                if ok3:
                    sought_png = enc3.tobytes()
                    sought_name = crop_path.name
    except Exception:
        sought_png = None

    with ui.container(border=True):
        ui.markdown(f"**Region** `{reg_name}` · live crop vs template")
        c_found, c_sought = ui.columns(2, gap="medium", vertical_alignment="top")
        cap_max = _REGION_CROP_MAX_SIDE
        with c_found:
            ui.caption("Live (from screenshot)")
            if found_png is not None:
                fitted2, native2, _ = png_bytes_fitted(found_png, cap_max)
                ui.image(
                    fitted2,
                    caption=f"{native2[0]}×{native2[1]} px",
                    width="stretch",
                )
            else:
                ui.caption("—")
        with c_sought:
            ui.caption("Template (reference crop)")
            if sought_png is not None:
                fitted3, native3, _ = png_bytes_fitted(sought_png, cap_max)
                ui.image(
                    fitted3,
                    caption=f"{sought_name or reg_name} · {native3[0]}×{native3[1]} px",
                    width="stretch",
                )
            else:
                ui.caption("—")


_NOTIFICATION_LEVEL_ICON: dict[str, str] = {
    "success": "✅",
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "❌",
}


@st.fragment(run_every=timedelta(seconds=1))
def _render_ui_notifications(inst: str) -> None:
    """Drain ``wos:ui:notifications:<inst>`` into ``st.toast`` once per tab.

    Producers (e.g. DSL ``exec`` handlers via :func:`ui.notifications.push_ui_notification`)
    push transient events; this fragment surfaces each event id at most once
    per Streamlit session via a ``session_state`` seen-set, so refreshes do not
    re-fire toasts and multiple tabs each get notified.
    """
    seen_key = f"wos_ui_notifications_seen::{inst}"
    seen: set[str] = st.session_state.setdefault(seen_key, set())
    events = pop_new_notifications(client, inst, seen=seen)
    if not events:
        return
    for ev in events:
        eid = str(ev.get("id") or "").strip()
        if not eid:
            continue
        seen.add(eid)
        msg = str(ev.get("message") or "").strip()
        if not msg:
            continue
        icon = _NOTIFICATION_LEVEL_ICON.get(
            str(ev.get("level") or "info").strip().lower(), "ℹ️"
        )
        try:
            st.toast(msg, icon=icon)
        except Exception:
            # Streamlit rejects emoji-incompatible icons on some builds — fall
            # back to a plain toast so the message still surfaces.
            st.toast(msg)


@st.fragment(run_every=timedelta(seconds=1))
def _heartbeat() -> None:
    enabled = str(client.get(enabled_key) or "").strip().lower() in {"1", "true", "yes", "on"}
    if enabled:
        client.set(hb_key, str(time.time()), ex=5)
    else:
        # If disabled, remove heartbeat so bot doesn't wait for approvals.
        client.delete(hb_key)
    has_current = bool(client.get(current_key))
    st.caption(
        f"Approval mode: **{'ON' if enabled else 'OFF'}** · "
        f"Heartbeat: **{'ON' if enabled else 'OFF'}** (ttl≈5s when ON) · "
        f"Pending request: **{'YES' if has_current else 'NO'}**."
    )


_CLICK_APPROVAL_PENDING_SNAP = "click_approvals_pending_snap"


@st.fragment(run_every=timedelta(seconds=1))
def _fragment_sync_pending_presence(inst: str) -> None:
    """Full rerun when a pending request appears or clears (switch idle ↔ pending layout)."""
    snap_k = f"{_CLICK_APPROVAL_PENDING_SNAP}::{inst}"
    ck = f"wos:ui:click_approval:current:{inst}"
    has_pending = bool(client.get(ck))
    prev = st.session_state.get(snap_k)
    if prev is not None and prev != has_pending:
        st.session_state[snap_k] = has_pending
        st.rerun()
    st.session_state[snap_k] = has_pending


@st.fragment(run_every=timedelta(seconds=1))
def _fragment_idle_screenshot_column(inst: str) -> None:
    """Rolling preview only — does not rerun the Approvals / overlay-probe column."""
    st.subheader("Screenshot")
    _render_preview_with_point(instance_id=inst, x=None, y=None, payload=None, where=st)


def _render_dsl_step_audit(ctx: dict[str, object]) -> None:
    """Last DSL ``match`` / ``ocr`` snapshot (from Redis), copied into approval ``context``."""
    mr = str(ctx.get("dsl_last_match_region") or "").strip()
    ms = str(ctx.get("dsl_last_match_score") or "").strip()
    mt = str(ctx.get("dsl_last_match_threshold") or "").strip()
    mm = str(ctx.get("dsl_last_match_matched") or "").strip()
    md = str(ctx.get("dsl_last_match_detail") or "").strip()
    ma = str(ctx.get("dsl_last_match_at") or "").strip()

    ox_r = str(ctx.get("dsl_last_ocr_region") or "").strip()
    ox_store = str(ctx.get("dsl_last_ocr_store") or "").strip()
    ox_status = str(ctx.get("dsl_last_ocr_status") or "").strip()
    ox_thr = str(ctx.get("dsl_last_ocr_threshold") or "").strip()
    ox_conf = str(ctx.get("dsl_last_ocr_confidence") or "").strip()
    ox_raw = str(ctx.get("dsl_last_ocr_raw_text") or "").strip()
    ox_val = str(ctx.get("dsl_last_ocr_value") or "").strip()
    ox_at = str(ctx.get("dsl_last_ocr_at") or "").strip()

    if not mr and not ox_r and not ox_status:
        return

    def _age_line(ts: str) -> str:
        if not ts:
            return ""
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))
        except (TypeError, ValueError, OSError):
            return ts

    with st.expander("DSL · last `match` / `ocr` (before this step)", expanded=True):
        st.caption(
            "Redis audit from `DslScenarioTask` (fields `dsl_last_*` on the instance state hash)."
        )
        if mr:
            st.markdown("**Last YAML `match:`**")
            passed = "yes" if mm == "1" else ("no" if mm == "0" else "—")
            lines = [
                f"- Region: `{mr}`",
                f"- Score / threshold: `{ms or '—'}` / `{mt or '—'}` · passed: **{passed}**",
            ]
            if md:
                lines.append(f"- Detail: `{md}`")
            if ma:
                lines.append(f"- At: `{_age_line(ma)}`")
            st.markdown("\n".join(lines))
        else:
            st.caption("No `dsl_last_match_*` yet (no `match:` step ran on this instance).")

        if ox_r or ox_status:
            st.markdown("**Last YAML `ocr:`**")
            raw_disp = ox_raw.replace("\n", " ").strip()
            if len(raw_disp) > 180:
                raw_disp = raw_disp[:177] + "…"
            olines = [
                f"- Region → Redis field: `{ox_r or '—'}` → `{ox_store or '—'}`",
                f"- Status: **`{ox_status or '—'}`**",
                f"- Confidence / threshold: `{ox_conf or '—'}` / `{ox_thr or '—'}`",
                f"- Stored decoded value: `{ox_val or '—'}`",
                f"- Raw OCR text: `{raw_disp or '—'}`",
            ]
            if ox_at:
                olines.append(f"- At: `{_age_line(ox_at)}`")
            st.markdown("\n".join(olines))
        else:
            st.caption("No `dsl_last_ocr_*` yet (no `ocr:` step ran on this instance).")


def _render_idle_approvals_column(inst: str) -> None:
    st.subheader("Approvals")
    st.success("No pending click requests.")
    st.caption(
        "Clears **current_screen** in Redis (same as unknown / overlay `node: none`). "
        "Useful when the worker stuck on the wrong FSM screen."
    )
    st.caption(
        "Left **Screenshot** refreshes every second. This column does **not** auto-refresh — "
        "you can use filters and the rule select without losing focus."
    )
    if st.button(
        "Reset node to none (unknown)",
        width="stretch",
        key=f"reset-node-none-{inst}",
    ):
        state_key = f"wos:instance:{inst}:state"
        client.hset(state_key, "current_screen", "")
        st.toast("current_screen cleared.", icon="✓")
        st.rerun()
    with st.expander(
        "Idle: overlay threshold check (rolling PNG + labeling region)",
        expanded=False,
    ):
        _render_idle_overlay_threshold_probe(inst)


@st.fragment(run_every=timedelta(seconds=1))
def _fragment_pending_approval_columns(inst: str, *, curr_key: str) -> None:
    raw = client.get(curr_key)
    if not raw:
        st.rerun()
        return
    try:
        payload = json.loads(raw)
    except Exception:
        st.error("Invalid pending payload JSON. Clearing.")
        client.delete(curr_key)
        st.rerun()
        return

    col_img, col_events = st.columns([1, 1.25], gap="large")

    x = payload.get("x")
    y = payload.get("y")
    x_i = int(x) if isinstance(x, (int, float)) else None
    y_i = int(y) if isinstance(y, (int, float)) else None
    with col_img:
        st.subheader("Screenshot")
        _render_preview_with_point(instance_id=inst, x=x_i, y=y_i, payload=payload, where=st)

    with col_events:
        st.subheader("Approvals")
        req_type = str(payload.get("type") or "").strip().lower()
        ctx0 = payload.get("context")

        def _scenario_block() -> None:
            if isinstance(ctx0, dict):
                scen_key = str(ctx0.get("scenario") or "").strip()
                if scen_key:
                    st.info(f"Scenario: `{scen_key}`")
                    st.page_link(
                        "views/wiki_scenarios.py",
                        label="Open scenario",
                        query_params={"q": scen_key},
                        width="stretch",
                    )

        if req_type == "set_node":
            sn = str(payload.get("set_node") or "").strip()
            st.caption("Pending **set_node** (needs approval).")
            _scenario_block()
            if sn:
                st.info(f"Will set **current_screen** to `{sn}`.")
        else:
            st.caption("Pending click / ADB input (needs approval).")
            reg_disp = str(payload.get("region") or "").strip()
            if not reg_disp and isinstance(ctx0, dict):
                reg_disp = str(ctx0.get("approval_region") or "").strip()
            if reg_disp:
                st.info(f"Target region / label: `{reg_disp}`")
            if isinstance(ctx0, dict):
                thr_c = str(ctx0.get("current_task_threshold") or "").strip()
                scr_c = str(ctx0.get("current_task_score") or "").strip()
                if thr_c or scr_c:
                    line = []
                    if thr_c:
                        line.append(f"threshold `{thr_c}`")
                    if scr_c:
                        line.append(f"match score `{scr_c}`")
                    st.caption("Overlay · " + " · ".join(line))
            _scenario_block()
        if isinstance(ctx0, dict):
            _render_dsl_step_audit(ctx0)
        with st.expander("Payload", expanded=True):
            st.code(json.dumps(payload, indent=2, ensure_ascii=False), language="json")

        c1, c2 = st.columns([1, 1], vertical_alignment="center")
        with c1:
            if st.button(
                "✅ Approve",
                type="primary",
                width="stretch",
                key=f"appr-{inst}",
            ):
                response_key = str(payload.get("response_key") or "").strip()
                if response_key:
                    client.set(response_key, "approve", ex=120)
                    client.delete(curr_key)
                st.rerun()
        with c2:
            if st.button("❌ Reject", width="stretch", key=f"rej-{inst}"):
                response_key = str(payload.get("response_key") or "").strip()
                if response_key:
                    client.set(response_key, "reject", ex=120)
                    client.delete(curr_key)
                st.rerun()


def _pending_request() -> None:
    """Idle: left column refreshes 1 Hz; Approvals + overlay probe stay static. Pending: both refresh."""
    inst = instance_id
    ck = current_key
    _fragment_sync_pending_presence(inst)

    raw = client.get(ck)
    if not raw:
        col_img, col_events = st.columns([1, 1.25], gap="large")
        with col_img:
            _fragment_idle_screenshot_column(inst)
        with col_events:
            _render_idle_approvals_column(inst)
        return

    _fragment_pending_approval_columns(inst, curr_key=ck)


st.caption(
    "Toggle approval mode below. **Default ON** when the Redis key is unset — worker waits for approve on each ADB input and each DSL **set_node** step until you turn this OFF."
)

enabled_now = click_approval_enabled(instance_id)
enabled_ui = st.toggle(
    "Approval mode (ON = require approve for ADB input and DSL set_node)",
    value=enabled_now,
    key=f"click_approvals_enabled::{instance_id}",
)
if enabled_ui != enabled_now:
    client.set(enabled_key, "1" if enabled_ui else "0")
    if not enabled_ui:
        client.delete(hb_key)
    st.rerun()

_header()
_render_ui_notifications(instance_id)
_heartbeat()
st.divider()
_pending_request()
