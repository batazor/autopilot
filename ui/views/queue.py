"""Priority queue viewer and scheduler nudge."""

from __future__ import annotations

import time

import streamlit as st

from ui.redis_client import (
    fetch_queue_rows,
    push_scheduler_command,
    remove_queue_task,
    require_redis_connection,
)

st.title("Task queue")

client = require_redis_connection()
now = time.time()
rows = fetch_queue_rows(client)

if not rows:
    st.info("Queue is empty.")
else:
    data: list[dict[str, object]] = []
    for r in rows:
        overdue = r.scheduled_at < now
        data.append(
            {
                "scheduled_iso": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(r.scheduled_at)
                ),
                "player_id": r.player_id,
                "instance_id": r.instance_id,
                "task_type": r.task_type,
                "region": r.region or "",
                "priority": r.priority,
                "cooperative": r.cooperative,
                "task_id": r.task_id,
                "overdue": overdue,
            }
        )

    st.dataframe(data, hide_index=True, width="stretch")

    st.subheader("Cancel task")
    tid = st.selectbox("task_id", [r.task_id for r in rows])
    if st.button("Remove from queue"):
        if remove_queue_task(client, tid):
            st.success("Removed.")
            st.rerun()
        else:
            st.error("Not found.")

st.divider()
if st.button("Run optimizer now (scheduler)"):
    push_scheduler_command(client, {"cmd": "optimize_now"})
    st.success("optimize_now sent to scheduler channel.")
