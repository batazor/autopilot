from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import streamlit as st

from .ctx import ClickApprovalsCtx
from .dsl_audit import render_dsl_step_audit
from .preview import render_preview_with_point


CLICK_APPROVAL_PENDING_SNAP = "click_approvals_pending_snap"


@st.fragment(run_every=timedelta(seconds=1))
def fragment_sync_pending_presence(*, inst: str, client: Any) -> None:
    """Full rerun when a pending request appears or clears (switch idle ↔ pending layout)."""
    snap_k = f"{CLICK_APPROVAL_PENDING_SNAP}::{inst}"
    ck = f"wos:ui:click_approval:current:{inst}"
    has_pending = bool(client.get(ck))
    prev = st.session_state.get(snap_k)
    if prev is not None and prev != has_pending:
        st.session_state[snap_k] = has_pending
        st.rerun()
    st.session_state[snap_k] = has_pending


@st.fragment(run_every=timedelta(seconds=1))
def fragment_pending_approval_columns(
    *, ctx: ClickApprovalsCtx, client: Any, inst: str, curr_key: str
) -> None:
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
        render_preview_with_point(
            ctx=ctx,
            instance_id=inst,
            x=x_i,
            y=y_i,
            payload=payload,
            where=st,
        )

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
            render_dsl_step_audit(ctx0)

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

