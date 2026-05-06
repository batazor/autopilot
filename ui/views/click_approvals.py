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

import cv2
import numpy as np
import streamlit as st

from config.loader import load_settings
from ui.preview_display import png_bytes_fitted
from ui.redis_client import get_instance_state, require_redis_connection
from ui.reference_preview import load_rolling_instance_preview

settings = load_settings()
client = require_redis_connection()

inst_ids = [i.instance_id for i in settings.instances]
if not inst_ids:
    st.info("No instances in config.")
    st.stop()

instance_id = st.selectbox("Instance", inst_ids, key="click_approval_instance")

hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
current_key = f"wos:ui:click_approval:current:{instance_id}"

row = get_instance_state(client, instance_id)
node = (row.get("current_screen") or "").strip() or "—"
st.title(f"Click approvals · {instance_id} · node: {node}")

_PREVIEW_MAX_SIDE = 360


def _render_preview_with_point(
    *,
    instance_id: str,
    x: int | None,
    y: int | None,
) -> None:
    png, rel, mtime = load_rolling_instance_preview(instance_id)
    if png is None:
        st.info(f"No rolling preview yet for `{instance_id}`.")
        return

    # Decode PNG to draw crosshair in pixel coords.
    arr = np.frombuffer(png, dtype=np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        st.warning("Could not decode rolling PNG.")
        return

    h, w = int(bgr.shape[0]), int(bgr.shape[1])
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
        st.warning("Could not encode preview image.")
        return
    out_png = enc.tobytes()
    fitted, native, _disp = png_bytes_fitted(out_png, _PREVIEW_MAX_SIDE)
    cap = f"{rel or instance_id} · {native[0]}×{native[1]}"
    if mtime is not None:
        cap = f"{cap} · {time.strftime('%H:%M:%S', time.localtime(mtime))}"
    if x is not None and y is not None:
        cap = f"{cap} · target=({x},{y})"
    st.image(fitted, caption=cap, width=_PREVIEW_MAX_SIDE)


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
    raw = client.get(current_key)
    if not raw:
        _render_preview_with_point(instance_id=instance_id, x=None, y=None)
        st.success("No pending click requests.")
        return

    st.subheader("Pending click")
    try:
        payload = json.loads(raw)
    except Exception:
        st.error("Invalid pending payload JSON. Clearing.")
        client.delete(current_key)
        return

    x = payload.get("x")
    y = payload.get("y")
    x_i = int(x) if isinstance(x, (int, float)) else None
    y_i = int(y) if isinstance(y, (int, float)) else None
    _render_preview_with_point(instance_id=instance_id, x=x_i, y=y_i)
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

_heartbeat()
st.divider()
_pending_request()
