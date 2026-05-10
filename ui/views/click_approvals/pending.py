from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import streamlit as st

from .common import (
    active_player_state_flat,
    labeling_query_params_for_area_region,
    load_area_doc,
    scenario_display_name,
)
from .ctx import ClickApprovalsCtx
from .preview import render_preview_with_point

CLICK_APPROVAL_PENDING_SNAP = "click_approvals_pending_snap"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").strip()
    return str(value).strip()


def _as_float(value: Any) -> float:
    try:
        return float(_as_text(value))
    except (TypeError, ValueError):
        return 0.0


def _is_stale_from_previous_worker(payload: dict[str, Any], row: dict[str, Any]) -> bool:
    status = _as_text(payload.get("status")).lower()
    if status and status != "waiting":
        return False
    created_at = _as_float(payload.get("created_at"))
    worker_started_at = _as_float(row.get("worker_started_at"))
    return created_at > 0 and worker_started_at > 0 and created_at < worker_started_at


def _payload_action_label(payload: dict[str, Any]) -> str:
    kind = str(payload.get("type") or "").strip().lower()
    if kind == "set_node":
        node = str(payload.get("set_node") or "").strip()
        return f"set node -> {node}" if node else "set node"
    if kind == "swipe":
        if str(payload.get("gesture") or "").strip().lower() == "long_press":
            return "long press"
        try:
            x1 = int(payload.get("x1") or 0)
            y1 = int(payload.get("y1") or 0)
            x2 = int(payload.get("x2") or 0)
            y2 = int(payload.get("y2") or 0)
            if x1 == x2 and y1 == y2:
                return "long press"
        except (TypeError, ValueError):
            pass
        return "swipe"
    if kind == "type_text":
        return "type text"
    if kind == "tap":
        return "click"
    return kind or "действие"


def _render_labeling_region_link(
    *,
    ctx: ClickApprovalsCtx,
    client: Any,
    inst: str,
    reg_name: str,
) -> None:
    reg = str(reg_name or "").strip()
    if not reg:
        return
    area_doc = load_area_doc(ctx.area_path)
    state_flat = active_player_state_flat(client=client, instance_id=inst)
    qp = labeling_query_params_for_area_region(area_doc, reg, state_flat=state_flat)
    if not qp:
        return
    label_reg = qp.get("region") or reg
    st.page_link(
        "views/labeling.py",
        label=f"Open Labeling for `{label_reg}`",
        query_params=qp,
        width="stretch",
    )


def _is_navigation_approval(payload: dict[str, Any], ctx0: object) -> bool:
    src = str(payload.get("approval_source") or "").strip().lower()
    if src == "navigation":
        return True
    if isinstance(ctx0, dict):
        return str(ctx0.get("approval_source") or "").strip().lower() == "navigation"
    return False


def _clear_stale_pending_after_restart(
    *, client: Any, inst: str, curr_key: str, raw: Any
) -> bool:
    try:
        payload = json.loads(_as_text(raw))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    try:
        row_raw = client.hgetall(f"wos:instance:{inst}:state") or {}
    except Exception:
        return False
    row = {_as_text(k): _as_text(v) for k, v in row_raw.items()}
    if not _is_stale_from_previous_worker(payload, row):
        return False

    response_key = _as_text(payload.get("response_key"))
    request_id = _as_text(payload.get("request_id")) or "unknown"
    client.delete(curr_key)
    if response_key:
        client.delete(response_key)
    st.toast(f"Cleared stale approval from previous bot run: `{request_id}`")
    return True


@st.fragment(run_every=timedelta(seconds=1))
def fragment_sync_pending_presence(*, inst: str, client: Any) -> None:
    """Full rerun when a pending request appears or clears (switch idle ↔ pending layout)."""
    snap_k = f"{CLICK_APPROVAL_PENDING_SNAP}::{inst}"
    ck = f"wos:ui:click_approval:current:{inst}"
    raw = client.get(ck)
    if raw and _clear_stale_pending_after_restart(client=client, inst=inst, curr_key=ck, raw=raw):
        raw = None
    has_pending = bool(raw)
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
    if _clear_stale_pending_after_restart(client=client, inst=inst, curr_key=curr_key, raw=raw):
        st.rerun()
        return
    try:
        payload = json.loads(_as_text(raw))
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
    # Swipe payloads historically had only x1/y1/x2/y2 — no tap-style crosshair.
    if x_i is None or y_i is None:
        try:
            if str(payload.get("type") or "").strip().lower() == "swipe":
                sx1 = int(payload.get("x1") or 0)
                sy1 = int(payload.get("y1") or 0)
                sx2 = int(payload.get("x2") or 0)
                sy2 = int(payload.get("y2") or 0)
                if sx1 == sx2 and sy1 == sy2:
                    x_i, y_i = sx1, sy1
        except (TypeError, ValueError):
            pass
    with col_img:
        st.subheader("Screenshot")
        source_key = f"click_approval_show_captured::{inst}"
        show_captured = bool(st.session_state.get(source_key, False))
        render_preview_with_point(
            ctx=ctx,
            instance_id=inst,
            x=x_i,
            y=y_i,
            payload=payload,
            where=st,
            client=client,
            image_source="capture" if show_captured else "live",
            source_toggle_key=source_key,
            source_toggle_value=show_captured,
        )

    with col_events:
        st.subheader("Approvals")
        req_type = str(payload.get("type") or "").strip().lower()
        ctx0 = payload.get("context")

        def _scenario_block() -> None:
            if isinstance(ctx0, dict):
                scen_key = str(ctx0.get("scenario") or "").strip()
                if scen_key:
                    st.info(f"Scenario: `{scenario_display_name(scen_key)}`")
                    st.page_link(
                        "views/scenarios.py",
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
        elif req_type == "diagnostic":
            st.caption("Pending diagnostic pause (needs approval).")
            _scenario_block()
            diag = str(payload.get("diagnostic") or "").strip()
            reg_disp = str(payload.get("region") or "").strip()
            if diag == "while_match_no_iterations":
                st.info("`while_match` matched zero times. Approve retries later; reject stops.")
            if reg_disp:
                st.info(f"Region under inspection: `{reg_disp}`")
                _render_labeling_region_link(ctx=ctx, client=client, inst=inst, reg_name=reg_disp)
            attempts = str(payload.get("attempts") or "").strip()
            interval = str(payload.get("interval") or "").strip()
            if attempts:
                suffix = f" · interval `{interval}s`" if interval else ""
                st.caption(f"Initial probes `{attempts}`{suffix}")
        else:
            st.caption("Pending click / ADB input (needs approval).")
            reg_disp = str(payload.get("region") or "").strip()
            if not reg_disp and isinstance(ctx0, dict):
                reg_disp = str(ctx0.get("approval_region") or "").strip()
            is_navigation = _is_navigation_approval(payload, ctx0)
            if is_navigation:
                nav_from = (
                    str(ctx0.get("approval_from_screen") or "").strip()
                    if isinstance(ctx0, dict)
                    else ""
                )
                nav_to = (
                    str(ctx0.get("approval_to_screen") or "").strip()
                    if isinstance(ctx0, dict)
                    else ""
                )
                route = f" · `{nav_from}` -> `{nav_to}`" if nav_from or nav_to else ""
                if reg_disp:
                    st.warning(f"Navigation click target: `{reg_disp}`{route}")
                    _render_labeling_region_link(
                        ctx=ctx,
                        client=client,
                        inst=inst,
                        reg_name=reg_disp,
                    )
                elif route:
                    st.warning(f"Navigation click{route}")
                _scenario_block()
                with st.expander(f"Payload · {_payload_action_label(payload)}", expanded=False):
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
                return
            if reg_disp:
                st.info(f"Target region / label: `{reg_disp}`")
                _render_labeling_region_link(ctx=ctx, client=client, inst=inst, reg_name=reg_disp)
            if isinstance(ctx0, dict):
                thr_c = str(ctx0.get("current_task_threshold") or "").strip()
                scr_c = str(ctx0.get("current_task_score") or "").strip()
                txt_c = str(ctx0.get("current_task_text") or "").strip()
                conf_c = str(ctx0.get("current_task_confidence") or "").strip()
                if txt_c:
                    line = [f"text `{txt_c}`"]
                    if conf_c:
                        line.append(f"conf `{conf_c}`")
                    st.caption("Overlay(text) · " + " · ".join(line))
                elif thr_c or scr_c:
                    line = []
                    if thr_c:
                        line.append(f"threshold `{thr_c}`")
                    if scr_c:
                        line.append(f"match score `{scr_c}`")
                    st.caption("Overlay · " + " · ".join(line))
            _scenario_block()

        with st.expander(f"Payload · {_payload_action_label(payload)}", expanded=False):
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
