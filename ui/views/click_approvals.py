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

from datetime import timedelta
from pathlib import Path

import streamlit as st

from actions.tap import click_approval_enabled
from analysis.overlay_manifest import default_analyze_yaml_path
from config.loader import load_settings
from ui.redis_client import require_redis_connection
from ui.views.click_approvals.chrome import (
    render_header,
    render_heartbeat,
    render_reset_block,
    render_ui_notifications,
)
from ui.views.click_approvals.ctx import ClickApprovalsCtx
from ui.views.click_approvals.idle_overlay_probe import render_idle_overlay_probe
from ui.views.click_approvals.pending import (
    fragment_pending_approval_columns,
    fragment_sync_pending_presence,
)
from ui.views.click_approvals.preview import render_preview_with_point

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
render_reset_block(client=client)

hb_key = f"wos:ui:click_approval:heartbeat:{instance_id}"
enabled_key = f"wos:ui:click_approval:enabled:{instance_id}"
current_key = f"wos:ui:click_approval:current:{instance_id}"

_PREVIEW_MAX_SIDE = 360
# Overlay probe: full-frame debug fit (search ROI / match / tap).
_PROBE_OVERLAY_MAX_SIDE = 900
# Thumbnails below main shot: keep each column narrower so the pair does not overflow.
_REGION_CROP_MAX_SIDE = 220

_CTX = ClickApprovalsCtx(
    instance_id=instance_id,
    repo_root=_REPO,
    area_path=_AREA,
    analyze_path=_ANALYZE,
    preview_max_side=_PREVIEW_MAX_SIDE,
    probe_overlay_max_side=_PROBE_OVERLAY_MAX_SIDE,
    region_crop_max_side=_REGION_CROP_MAX_SIDE,
)


def _render_overlay_threshold_probe(instance_id: str) -> None:
    del instance_id
    return render_idle_overlay_probe(ctx=_CTX, client=client)


def _render_preview_with_point(
    *,
    instance_id: str,
    x: int | None,
    y: int | None,
    payload: dict[str, object] | None = None,
    where: object | None = None,
) -> None:
    ui = where if where is not None else st
    return render_preview_with_point(
        ctx=_CTX,
        instance_id=instance_id,
        x=x,
        y=y,
        payload=payload,  # type: ignore[arg-type]
        where=ui,
    )


@st.fragment(run_every=timedelta(seconds=1))
def _fragment_idle_screenshot_column(inst: str) -> None:
    """Rolling preview only — does not rerun the Approvals / overlay-probe column."""
    st.subheader("Screenshot")
    _render_preview_with_point(instance_id=inst, x=None, y=None, payload=None, where=st)


def _render_idle_approvals_column(inst: str) -> None:
    st.subheader("Approvals")
    st.success("No pending click requests.")
    st.caption(
        "Clears **current_screen** in Redis (same as unknown / overlay `screens: [none]`). "
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
        try:
            st.toast("current_screen cleared.", icon="✅")
        except Exception:
            st.toast("current_screen cleared.")
        st.rerun()


def _render_overlay_probe_section(inst: str) -> None:
    with st.expander(
        "Overlay threshold check",
        expanded=False,
    ):
        st.caption("Rolling PNG + labeling region.")
        _render_overlay_threshold_probe(inst)


def _fragment_pending_approval_columns(inst: str, *, curr_key: str) -> None:
    return fragment_pending_approval_columns(ctx=_CTX, client=client, inst=inst, curr_key=curr_key)


def _pending_request() -> None:
    """Idle: left refresh 1 Hz; pending refreshes both."""
    inst = instance_id
    ck = current_key
    fragment_sync_pending_presence(inst=inst, client=client)

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
    "Toggle approval mode below. **Default ON** when the Redis key is unset — "
    "worker waits for approve on each ADB input and each DSL **set_node** step "
    "until you turn this OFF."
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

ocr_cfg = getattr(settings, "ocr", None)
ocr_url = str(getattr(ocr_cfg, "url", "") or "")
render_header(ctx=_CTX, client=client, ocr_url=ocr_url)
render_ui_notifications(instance_id, client=client)
render_heartbeat(ctx=_CTX, client=client)
st.divider()
_pending_request()
st.divider()
_render_overlay_probe_section(instance_id)
