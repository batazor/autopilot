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
from config.loader import load_settings
from layout.area_lookup import screen_region_by_name
from layout.crop_paths import exported_crop_png
from ui.preview_display import png_bytes_fitted
from ui.redis_client import get_instance_state, require_redis_connection
from ui.reference_preview import load_rolling_instance_preview

settings = load_settings()
client = require_redis_connection()

_REPO = Path(__file__).resolve().parents[2]
_AREA = _REPO / "area.json"

inst_ids = [i.instance_id for i in settings.instances]
if not inst_ids:
    st.info("No instances in config.")
    st.stop()

instance_id = st.selectbox("Instance", inst_ids, key="click_approval_instance")

hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
current_key = f"wos:ui:click_approval:current:{instance_id}"

_PREVIEW_MAX_SIDE = 360
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


@st.fragment(run_every=timedelta(seconds=1))
def _header() -> None:
    row = get_instance_state(client, instance_id)
    node = (row.get("current_screen") or "").strip() or "—"
    st.title(f"Click approvals · {instance_id} · node: {node}")
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


@st.fragment(run_every=timedelta(seconds=1))
def _pending_request() -> None:
    col_img, col_events = st.columns([1, 1.25], gap="large")
    raw = client.get(current_key)
    if not raw:
        with col_img:
            st.subheader("Screenshot")
            _render_preview_with_point(instance_id=instance_id, x=None, y=None, payload=None, where=st)
        with col_events:
            st.subheader("Approvals")
            st.success("No pending click requests.")
            st.caption(
                "Clears **current_screen** in Redis (same as unknown / overlay `node: none`). "
                "Useful when the worker stuck on the wrong FSM screen."
            )
            if st.button(
                "Reset node to none (unknown)",
                width="stretch",
                key=f"reset-node-none-{instance_id}",
            ):
                state_key = f"wos:instance:{instance_id}:state"
                client.hset(state_key, "current_screen", "")
                st.toast("current_screen cleared.", icon="✓")
                st.rerun()
        return

    with col_events:
        st.subheader("Approvals")
    try:
        payload = json.loads(raw)
    except Exception:
        with col_events:
            st.error("Invalid pending payload JSON. Clearing.")
        client.delete(current_key)
        return

    x = payload.get("x")
    y = payload.get("y")
    x_i = int(x) if isinstance(x, (int, float)) else None
    y_i = int(y) if isinstance(y, (int, float)) else None
    with col_img:
        st.subheader("Screenshot")
        _render_preview_with_point(instance_id=instance_id, x=x_i, y=y_i, payload=payload, where=st)

    with col_events:
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
            _scenario_block()
        with st.expander("Payload", expanded=True):
            st.code(json.dumps(payload, indent=2, ensure_ascii=False), language="json")

        c1, c2 = st.columns([1, 1], vertical_alignment="center")
        with c1:
            if st.button(
                "✅ Approve",
                type="primary",
                width="stretch",
                key=f"appr-{instance_id}",
            ):
                response_key = str(payload.get("response_key") or "").strip()
                if response_key:
                    client.set(response_key, "approve", ex=120)
                    client.delete(current_key)
                st.rerun()
        with c2:
            if st.button("❌ Reject", width="stretch", key=f"rej-{instance_id}"):
                response_key = str(payload.get("response_key") or "").strip()
                if response_key:
                    client.set(response_key, "reject", ex=120)
                    client.delete(current_key)
                st.rerun()


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
_heartbeat()
st.divider()
_pending_request()
