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

import streamlit as st

from adb import click_approval_enabled
from config.loader import load_settings
from config.paths import repo_root
from ui.adb_query import canonical_serial, live_serials, port_scan_connect
from ui.redis_client import require_redis_connection
from ui.views._debug_scenarios_progress import render_active_scenario_progress
from ui.views.click_approvals.chrome import (
    render_header,
    render_heartbeat,
    render_node_player_caption,
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

_REPO = repo_root()
_AREA = _REPO / "area.json"
all_instances = list(settings.instances)
if not all_instances:
    st.info("No instances in `db/devices.yaml`.")
    st.stop()

# Filter the dropdown to instances whose ADB serial is currently in `device`
# state. Worker for an offline instance can't capture screens or accept taps,
# so listing it in this picker would only invite a stale selection.
_live = live_serials()
active_instances = [
    inst for inst in all_instances
    if canonical_serial(inst.bluestacks_window_title) in _live
]
if not active_instances:
    st.warning(
        "No active ADB devices. Click **Refresh** below to re-query `adb devices`, "
        "or open the **ADB** page to (re)connect emulators."
    )
    _cols = st.columns([1, 1, 5])
    with _cols[0]:
        if st.button(
            "🔄 Refresh",
            key="click_approval_adb_refresh",
            width="stretch",
            help=(
                "Port-scans 127.0.0.1:5555-5700 with `adb connect` to reattach "
                "disconnected emulators, then re-queries `adb devices`."
            ),
        ):
            # `adb devices -l` alone won't reattach an emulator that the ADB
            # server has dropped — match the ADB page's Refresh and run the
            # port-scan + connect first, then rerun to pick up the new state.
            port_scan_connect(5555, 5700)
            st.rerun()
    with _cols[1]:
        st.page_link(
            "views/adb_devices.py",
            label="Open ADB",
            help="ADB device list and reconnect controls.",
            icon="📱",
            width="stretch",
        )
    st.stop()

inst_ids = [i.instance_id for i in active_instances]
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
    render_node_player_caption(ctx=_CTX, client=client)
    st.success("No pending click requests.")
    st.caption(
        "Clears **current_screen** in Redis (same as unknown / overlay `screens: [none]`). "
        "Useful when the worker is stuck on the wrong node."
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
# Live progress for the scenario currently running on this instance — pulled
# from Redis ``current_scenario`` + ``last_active_scenario_step`` so the bar
# stays visible whether or not there's a pending approval card.
render_active_scenario_progress(
    client=client,
    instance_id=instance_id,
    repo_root=_REPO,
)
st.divider()
_pending_request()
st.divider()
_render_overlay_probe_section(instance_id)
