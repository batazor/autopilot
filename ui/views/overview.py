"""Dashboard overview: instances table, queue summary, quick actions."""

from __future__ import annotations

import time

import streamlit as st
from streamlit_autorefresh import st_autorefresh

from config.loader import load_settings
from ui.bot_services import ensure_embedded_bot
from ui.redis_client import (
    count_claimed_slots,
    count_queue_tasks,
    get_instance_state,
    get_player_fsm,
    push_instance_command,
    require_redis_connection,
)

ensure_embedded_bot()

st_autorefresh(interval=2000, key="overview_refresh")

st.title("Overview")

if "overview_feedback" not in st.session_state:
    st.session_state.overview_feedback = None


def _set_feedback(text: str) -> None:
    st.session_state.overview_feedback = text


def _format_elapsed(seconds: float) -> str:
    sec = int(max(0.0, seconds))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _elapsed_since(ts_str: str) -> str | None:
    ts_str = ts_str.strip()
    if not ts_str:
        return None
    try:
        delta = time.time() - float(ts_str)
    except ValueError:
        return None
    return _format_elapsed(delta)


def _device_status_dot(row: dict[str, str]) -> str:
    """Green = worker OK and not paused; red = offline, paused, crashed, or restarting."""
    if not row:
        return "🔴"
    if row.get("paused") == "1":
        return "🔴"
    st_val = (row.get("state") or "").lower()
    if st_val == "restarting":
        return "🔴"
    if st_val == "crashed":
        return "🔴"
    return "🟢"


def _task_cell(row: dict[str, str]) -> str:
    t = (row.get("current_task_type") or "").strip()
    if not t:
        return "—"
    elapsed = _elapsed_since(row.get("current_task_started_at") or "")
    if elapsed:
        return f"{t} ({elapsed})"
    return t


_DEVICES_HELP = (
    "Device: instance id from settings. Status: green = worker running; "
    "red = no Redis state, paused, crashed, or restarting. "
    "Player: active account after a successful switch. "
    "Task: queue task type while it runs (with elapsed time). "
    "Session: uptime since the worker connected to Redis "
    "(resets when the worker restarts). "
    "Row: pause **or** resume (one control by worker state), open Instance page."
)


fb = st.session_state.overview_feedback
if fb:
    st.info(fb)

settings = load_settings()
client = require_redis_connection()

n_inst = len(settings.instances)
n_players = sum(len(i.player_ids) for i in settings.instances)
q = count_queue_tasks(client)
claimed = count_claimed_slots(client)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Instances", n_inst)
c2.metric("Players", n_players)
c3.metric("Queue tasks", q)
c4.metric("Cooperative locks", claimed)

st.divider()

st.subheader("Devices", help=_DEVICES_HELP)
st.caption(
    "**Worker is running** when Status is 🟢 and **Session** shows uptime "
    "(Redis `wos:instance:<id>:state`). Embedded bot starts from **`ui/app.py`** "
    "or when this Overview/Instance page loads standalone. "
    "**⏸ Pause** stops dequeuing tasks and **ADB rolling preview PNG** until **▶ Resume**."
)

# Column weights: data + compact icon actions
# (Streamlit cannot embed buttons in st.dataframe cells).
_TABLE_COLS = [2.2, 0.45, 1.25, 2.35, 1.35, 0.5, 0.48]

if settings.instances:
    hdr = st.columns(_TABLE_COLS)
    hdr[0].markdown("**Device**")
    hdr[1].markdown("**Status**")
    hdr[2].markdown("**Player**")
    hdr[3].markdown("**Task**")
    hdr[4].markdown("**Session**")
    hdr[5].markdown("**▶⏸**")
    hdr[6].markdown("**🔗**")

    for inst in settings.instances:
        row = get_instance_state(client, inst.instance_id)
        active = (row.get("active_player") or "").strip() or "—"
        session_uptime = _elapsed_since(row.get("worker_started_at") or "") or "—"
        dot = _device_status_dot(row)
        task_c = _task_cell(row)
        paused = row.get("paused") == "1"

        r = st.columns(_TABLE_COLS)
        r[0].markdown(inst.instance_id)
        r[1].markdown(dot)
        r[2].markdown(active)
        r[3].markdown(task_c)
        r[4].markdown(session_uptime)
        with r[5]:
            if paused:
                if st.button(
                    "▶",
                    key=f"ov-resume-{inst.instance_id}",
                    help="Resume this instance worker (starts dequeuing tasks again).",
                    use_container_width=True,
                ):
                    push_instance_command(client, inst.instance_id, {"cmd": "resume"})
                    _set_feedback(f"`{inst.instance_id}`: resume sent to worker.")
            else:
                if st.button(
                    "⏸",
                    key=f"ov-pause-{inst.instance_id}",
                    help="Pause this instance worker (stops dequeuing tasks until resumed).",
                    use_container_width=True,
                ):
                    push_instance_command(client, inst.instance_id, {"cmd": "pause"})
                    _set_feedback(f"`{inst.instance_id}`: pause sent to worker.")
        with r[6]:
            st.page_link(
                "views/instance.py",
                label="🔗",
                query_params={"instance_id": inst.instance_id},
                help=(
                    "Instance — screenshots, queue commands, FSM history "
                    f"for `{inst.instance_id}`."
                ),
                use_container_width=True,
            )

    st.divider()
    for inst in settings.instances:
        with st.expander(f"Players & FSM · {inst.instance_id}", expanded=False):
            pcols = st.columns(min(4, max(1, len(inst.player_ids))))
            for idx, pid in enumerate(inst.player_ids):
                with pcols[idx % len(pcols)]:
                    fsm = get_player_fsm(client, pid) or "unknown"
                    st.text(f"{pid}\n{fsm}")
        st.divider()
else:
    st.info("No instances in **config/settings.yaml**.")

if st.button("Dismiss banner"):
    st.session_state.overview_feedback = None
    st.rerun()
