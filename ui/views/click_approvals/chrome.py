from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

import streamlit as st

from ui.bot_services import restart_embedded_bot
from ui.notifications import pop_new_notifications
from ui.redis_client import get_instance_state

from .common import ocr_health_status
from .ctx import ClickApprovalsCtx


def _clear_click_approval_current_keys(*, client: Any) -> None:
    try:
        for key in client.scan_iter("wos:ui:click_approval:current:*"):
            raw = client.get(key)
            if raw:
                try:
                    import json

                    txt = raw.decode() if isinstance(raw, bytes) else str(raw)
                    payload = json.loads(txt)
                    response_key = str(payload.get("response_key") or "").strip()
                    if response_key:
                        client.delete(response_key)
                except Exception:
                    pass
            client.delete(key)
    except Exception:
        st.exception(Exception("Failed to clear click approval current keys"))
        st.stop()


def render_reset_block(*, client: Any) -> None:
    with st.expander("Reset", expanded=False):
        st.caption("Clears the task queue and restarts embedded bot workers/scheduler.")
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Clear queue", type="primary", key="click_approvals_clear_queue_btn"):
                try:
                    for key in client.scan_iter("wos:queue:*"):
                        k = str(key)
                        if ":running" in k:
                            continue
                        client.delete(k)
                except Exception:
                    st.exception(Exception("Failed to clear Redis queue keys (`wos:queue*`)"))
                    st.stop()
                st.success("Queue cleared.")
                st.rerun()
        with c2:
            if st.button("Restart bot", type="primary", key="click_approvals_restart_bot_btn"):
                _clear_click_approval_current_keys(client=client)
                restart_embedded_bot()
                st.success("Pending approval cleared and bot restart triggered.")
                st.rerun()

        if st.button(
            "Reset: clear queue + restart bot",
            type="primary",
            key="click_approvals_reset_btn",
            help="Does both actions above.",
        ):
            try:
                for key in client.scan_iter("wos:queue:*"):
                    k = str(key)
                    if ":running" in k:
                        continue
                    client.delete(k)
            except Exception:
                st.exception(Exception("Failed to clear Redis queue keys (`wos:queue*`)"))
                st.stop()
            _clear_click_approval_current_keys(client=client)
            restart_embedded_bot()
            st.success("Queue and pending approvals cleared; bot restart triggered.")
            st.rerun()


def _active_player_in_game_id(*, client: Any, inst: str) -> str:
    """OCR'd in-game `player_id` of the active bot account on `inst`."""
    row = get_instance_state(client, inst) or {}
    active = str(row.get("active_player") or "").strip()
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
def render_header(*, ctx: ClickApprovalsCtx, client: Any, ocr_url: str = "") -> None:
    row = get_instance_state(client, ctx.instance_id) or {}
    node = str(row.get("current_screen") or "").strip() or "—"
    pid_in_game = _active_player_in_game_id(client=client, inst=ctx.instance_id)

    st.title(f"Click approvals · {ctx.instance_id}")
    st.caption(f"node: `{node}` · player_id: `{pid_in_game}`")
    ok, detail = ocr_health_status(ocr_url)
    if not ok and str(ocr_url or "").strip():
        st.warning(
            f"OCR service is not available ({ocr_url}). "
            "Please start OCR (e.g. docker-compose) — otherwise screen OCR/detection may stall. "
            f"Details: {detail}"
        )


_NOTIFICATION_LEVEL_ICON: dict[str, str] = {
    "success": "✅",
    "info": "ℹ️",
    "warning": "⚠️",
    "error": "❌",
}

@st.fragment(run_every=timedelta(seconds=1))
def render_ui_notifications(inst: str, *, client: Any) -> None:
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
        icon = _NOTIFICATION_LEVEL_ICON.get(str(ev.get("level") or "info").strip().lower(), "ℹ️")
        try:
            st.toast(msg, icon=icon)
        except Exception:
            st.toast(msg)


@st.fragment(run_every=timedelta(seconds=1))
def render_heartbeat(*, ctx: ClickApprovalsCtx, client: Any) -> None:
    enabled = str(client.get(ctx.enabled_key) or "").strip().lower() in {"1", "true", "yes", "on"}
    if enabled:
        client.set(ctx.hb_key, str(time.time()), ex=5)
    else:
        client.delete(ctx.hb_key)
    has_current = bool(client.get(ctx.current_key))
    st.caption(
        f"Approval mode: **{'ON' if enabled else 'OFF'}** · "
        f"Heartbeat: **{'ON' if enabled else 'OFF'}** (ttl≈5s when ON) · "
        f"Pending request: **{'YES' if has_current else 'NO'}**."
    )
