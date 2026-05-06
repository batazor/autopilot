"""Priority queue viewer and scheduler nudge."""

from __future__ import annotations

import time
from datetime import timedelta

import streamlit as st

from ui.redis_client import (
    fetch_queue_rows,
    push_scheduler_command,
    remove_queue_task,
    require_redis_connection,
)

st.title("Task queue")

client = require_redis_connection()


@st.fragment(run_every=timedelta(seconds=3))
def _queue_fragment() -> None:
    now = time.time()
    rows = fetch_queue_rows(client)

    hdr, refresh_btn = st.columns([5, 1])
    with hdr:
        if rows:
            overdue_n = sum(1 for r in rows if r.scheduled_at < now)
            parts = [f"**{len(rows)}** task(s)"]
            if overdue_n:
                parts.append(f"**{overdue_n}** overdue")
            st.caption(" · ".join(parts) + " · refreshes every 3 s")
    with refresh_btn:
        if st.button("🔄", help="Refresh now", key="queue_refresh_btn", width="stretch"):
            st.rerun()

    if not rows:
        st.info("Queue is empty.")
        return

    # Filters
    all_players = sorted({r.player_id for r in rows if r.player_id})
    all_instances = sorted({r.instance_id for r in rows if r.instance_id})

    fc1, fc2 = st.columns(2)
    with fc1:
        sel_players = st.multiselect(
            "Player", all_players, placeholder="All players", key="queue_filter_player"
        )
    with fc2:
        sel_instances = st.multiselect(
            "Instance", all_instances, placeholder="All instances", key="queue_filter_instance"
        )

    if sel_players:
        rows = [r for r in rows if r.player_id in sel_players]
    if sel_instances:
        rows = [r for r in rows if r.instance_id in sel_instances]

    if not rows:
        st.info("No tasks match the current filter.")
        return

    data = []
    for r in rows:
        overdue = r.scheduled_at < now
        scheduled_str = time.strftime("%H:%M:%S", time.localtime(r.scheduled_at))
        data.append(
            {
                "_del": False,
                "scheduled": f"{scheduled_str} ⚠️" if overdue else scheduled_str,
                "player_id": r.player_id,
                "instance_id": r.instance_id,
                "task_type": r.task_type,
                "region": r.region or "",
                "priority": r.priority,
                "coop": r.cooperative,
                "task_id": r.task_id,
            }
        )

    edited = st.data_editor(
        data,
        column_config={
            "_del": st.column_config.CheckboxColumn("Del", width="small"),
            "scheduled": st.column_config.TextColumn("Scheduled", width="small"),
            "player_id": st.column_config.TextColumn("Player", width="small"),
            "instance_id": st.column_config.TextColumn("Instance", width="small"),
            "task_type": st.column_config.TextColumn("Task type"),
            "region": st.column_config.TextColumn("Region", width="small"),
            "priority": st.column_config.NumberColumn("Pri", width="small", format="%d"),
            "coop": st.column_config.CheckboxColumn("Coop", width="small"),
            "task_id": st.column_config.TextColumn("Task ID"),
        },
        disabled=("scheduled", "player_id", "instance_id", "task_type", "region", "priority", "coop", "task_id"),
        hide_index=True,
        width="stretch",
        num_rows="fixed",
        key="queue_data_editor",
    )

    # Collect selected task IDs from whatever st.data_editor returns.
    selected_ids: list[str] = []
    records: list[dict] = (
        edited.to_dict(orient="records") if hasattr(edited, "to_dict") else list(edited or [])
    )
    for row in records:
        if row.get("_del"):
            selected_ids.append(str(row["task_id"]))

    n = len(selected_ids)
    btn_label = f"Delete {n} selected" if n else "Delete selected"
    if st.button(
        btn_label,
        type="primary" if n else "secondary",
        disabled=not n,
        key="queue_delete_btn",
    ):
        removed = sum(1 for tid in selected_ids if remove_queue_task(client, tid))
        if removed:
            st.success(f"Removed {removed} task(s).")
        else:
            st.warning("None found — tasks may have already been processed.")
        st.rerun()


_queue_fragment()

st.divider()
if st.button("Run optimizer now (scheduler)"):
    push_scheduler_command(client, {"cmd": "optimize_now"})
    st.success("optimize_now sent to scheduler channel.")
