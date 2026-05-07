"""Per-click approval page: when open, bot clicks require explicit approval.

Contract:
- Page open => refreshes Redis heartbeat key (per instance).
- Worker input clicks:
  - If no heartbeat => do not click
  - If heartbeat present => publish single "current" request and wait for approve/reject
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

from config.loader import load_settings
from layout.area_lookup import screen_region_by_name
from ui.preview_display import png_bytes_fitted
from ui.redis_client import get_instance_state, require_redis_connection
from ui.reference_preview import load_rolling_instance_preview

settings = load_settings()
client = require_redis_connection()

_REPO = Path(__file__).resolve().parents[2]
_AREA = _REPO / "area.json"
_SCENARIOS = _REPO / "scenarios"

inst_ids = [i.instance_id for i in settings.instances]
if not inst_ids:
    st.info("No instances in config.")
    st.stop()

instance_id = st.selectbox("Instance", inst_ids, key="click_approval_instance")

hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
current_key = f"wos:ui:click_approval:current:{instance_id}"

_PREVIEW_MAX_SIDE = 360


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


def _detect_scenario_path_from_context(ctx: dict[str, object]) -> str | None:
    """Best-effort: infer scenario YAML path from current task context."""
    task_type = str(ctx.get("current_task_type") or "").strip()
    if not task_type:
        return None

    # DSL scenarios: current convention is `scenarios/imperative_drafts/main_city/<task_type>.yaml`
    cand = [
        _SCENARIOS / "imperative_drafts" / "main_city" / f"{task_type}.yaml",
        _SCENARIOS / "by_cron" / f"{task_type}.yaml",
        _SCENARIOS / f"{task_type}.yaml",
    ]
    for p in cand:
        try:
            if p.is_file():
                return p.relative_to(_REPO).as_posix()
        except OSError:
            continue
    return None


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

        # Double-stroke outline: black shadow + bright border.
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 0), 5)
        cv2.rectangle(img, (x0, y0), (x1, y1), (255, 255, 255), 3)
        cv2.rectangle(img, (x0, y0), (x1, y1), (0, 220, 255), 2)

        if label:
            font = cv2.FONT_HERSHEY_SIMPLEX
            text = str(label).strip()
            if not text:
                return
            (tw, th), base = cv2.getTextSize(text[:80], font, 0.5, 1)
            pad = 4
            bx0 = x0
            by1 = max(0, y0 - 6)
            by0 = max(0, by1 - (th + base + pad * 2))
            bx1 = min(w, bx0 + tw + pad * 2)
            # Background pill for readability.
            cv2.rectangle(img, (bx0, by0), (bx1, by1), (0, 0, 0), -1)
            cv2.rectangle(img, (bx0, by0), (bx1, by1), (0, 220, 255), 1)
            cv2.putText(
                img,
                text[:80],
                (bx0 + pad, by1 - pad - base),
                font,
                0.5,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )

    # Draw the zone we "found" (when available) + its confidence/score.
    # - For `tap_region`, the approval payload already contains pixel rect.
    # - For `overlay_tap`, we resolve `current_task_region` from context via `area.json`.
    if isinstance(payload, dict):
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
            task_type = str(ctx.get("current_task_type") or "").strip()
            score_s = str(ctx.get("current_task_score") or "").strip()
            thr_s = str(ctx.get("current_task_threshold") or "").strip()
            if task_type == "overlay_tap" and reg_name:
                area_doc = _load_area_doc()
                pair = screen_region_by_name(area_doc, reg_name)
                if pair is not None and isinstance(pair[1].get("bbox"), dict):
                    L, T, R, B = _pct_bbox_to_px_rect(pair[1]["bbox"], w, h)
                    label = reg_name
                    try:
                        if score_s:
                            label += f" score={float(score_s):.3f}"
                    except Exception:
                        pass
                    try:
                        if thr_s:
                            label += f" thr={float(thr_s):.3f}"
                    except Exception:
                        pass
                    _draw_focus_rect(bgr, x0=L, y0=T, x1=R, y1=B, label=label)
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
    ui.image(fitted, caption=cap, width=_PREVIEW_MAX_SIDE)


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
        st.caption("Pending click request (needs approval).")
        ctx0 = payload.get("context")
        if isinstance(ctx0, dict):
            scen = _detect_scenario_path_from_context(ctx0)
            if scen:
                st.info(f"Scenario click context: `{scen}`")
        with st.expander("Payload", expanded=True):
            st.code(json.dumps(payload, indent=2, ensure_ascii=False), language="json")

        c1, c2, c3 = st.columns([1, 1, 2], vertical_alignment="center")
        with c1:
            if st.button(
                "✅ Approve",
                type="primary",
                use_container_width=True,
                key=f"appr-{instance_id}",
            ):
                response_key = str(payload.get("response_key") or "").strip()
                if response_key:
                    client.set(response_key, "approve", ex=120)
                st.rerun()
        with c2:
            if st.button("❌ Reject", use_container_width=True, key=f"rej-{instance_id}"):
                response_key = str(payload.get("response_key") or "").strip()
                if response_key:
                    client.set(response_key, "reject", ex=120)
                st.rerun()
        with c3:
            if st.button("🗑 Drop (no response)", use_container_width=True, key=f"drop-{instance_id}"):
                client.delete(current_key)
                st.rerun()


st.caption(
    "Toggle approval mode below. When ON, bot waits for approve on every ADB input."
)

enabled_now = str(client.get(enabled_key) or "").strip().lower() in {"1", "true", "yes", "on"}
enabled_ui = st.toggle(
    "Approval mode (ON = require approve for every ADB input)",
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
