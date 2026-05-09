"""Priority queue viewer and scheduler nudge."""

from __future__ import annotations

import json
import time
from datetime import timedelta

import pandas as pd
import streamlit as st

from config.loader import load_settings
from ui.redis_client import (
    count_queue_tasks_for_instance,
    fetch_next_queue_row_for_instance,
    fetch_queue_history_rows,
    fetch_queue_rows,
    fetch_running_queue_row,
    push_scheduler_command,
    remove_queue_task,
    require_redis_connection,
)


def _rel_time(ts: float, now: float) -> str:
    """Return a human-readable relative time string."""
    delta = ts - now
    abs_s = abs(delta)
    if abs_s < 60:
        label = f"{int(abs_s)}s"
    elif abs_s < 3600:
        m, s = divmod(int(abs_s), 60)
        label = f"{m}m {s}s" if s else f"{m}m"
    else:
        h, rem = divmod(int(abs_s), 3600)
        label = f"{h}h {rem // 60}m" if rem else f"{h}h"
    return f"in {label}" if delta >= 0 else f"{label} ago"

st.title("Task queue")

client = require_redis_connection()


@st.fragment(run_every=timedelta(seconds=3))
def _queue_fragment() -> None:
    now = time.time()
    rows = fetch_queue_rows(client)
    settings = load_settings()

    hdr, refresh_btn = st.columns([5, 1])
    with hdr:
        inst_ids = [i.instance_id for i in settings.instances]
        running_rows = [
            (iid, fetch_running_queue_row(client, instance_id=iid)) for iid in inst_ids
        ]
        running_rows = [(iid, r) for iid, r in running_rows if r is not None and r.task_id]
        if running_rows:
            with st.expander("Running now (all instances)", expanded=True):
                for iid, r in running_rows:
                    dur = ""
                    if r.started_at > 0:
                        dur = f" · {_rel_time(r.started_at, now)}"
                    st.info(
                        f"**{iid}** · **{r.task_type}** · task_id `{r.task_id}` · "
                        f"player `{r.player_id or '—'}`"
                        + (f" · region `{r.region}`" if r.region else "")
                        + dur
                    )
        if rows:
            overdue_n = sum(1 for r in rows if r.scheduled_at < now)
            parts = [f"**{len(rows)}** task(s)"]
            if overdue_n:
                parts.append(f"**{overdue_n}** overdue")
            st.caption(" · ".join(parts) + " · refreshes every 3 s")
    with refresh_btn:
        if st.button("🔄", help="Refresh now", key="queue_refresh_btn", width="stretch"):
            st.rerun()

    # Per-instance glance: metric cards.
    inst_ids = [i.instance_id for i in settings.instances]
    if inst_ids:
        overdue_by_inst = {iid: 0 for iid in inst_ids}
        for r in rows:
            if r.instance_id in overdue_by_inst and r.scheduled_at < now:
                overdue_by_inst[r.instance_id] += 1

        metric_cols = st.columns(len(inst_ids))
        for col, iid in zip(metric_cols, inst_ids):
            size = count_queue_tasks_for_instance(client, instance_id=iid)
            next_row = fetch_next_queue_row_for_instance(client, instance_id=iid)
            overdue_n = overdue_by_inst.get(iid, 0)
            with col:
                with st.container(border=True):
                    st.markdown(f"**{iid}**")
                    c1, c2 = st.columns(2)
                    c1.metric("Queued", size)
                    c2.metric("Overdue", overdue_n)
                    if next_row is not None and next_row.scheduled_at:
                        st.caption(
                            f"Next: {_rel_time(next_row.scheduled_at, now)} · {next_row.task_type}"
                        )

    if not rows:
        st.info("Queue is empty.")
        return

    # Copy helper (Streamlit can't write to OS clipboard without a custom component).
    copy_slot = st.session_state.get("queue_copy_json", "")
    if copy_slot:
        with st.expander("Copied JSON (Cmd/Ctrl+C)", expanded=False):
            st.code(copy_slot, language="json")
            st.text_area(
                "Raw",
                value=copy_slot,
                height=140,
                key="queue_copy_json_area",
            )

    # Filters
    _DEVICE_LABEL = "(device)"
    all_players = sorted(
        {(r.player_id if r.player_id else _DEVICE_LABEL) for r in rows}
    )
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
        rows = [
            r for r in rows
            if (r.player_id if r.player_id else _DEVICE_LABEL) in sel_players
        ]
    if sel_instances:
        rows = [r for r in rows if r.instance_id in sel_instances]

    if not rows:
        st.info("No tasks match the current filter.")
        return

    st.markdown("**Queue items**")
    header = st.columns([0.55, 1.1, 1.0, 1.0, 1.4, 1.0, 0.7, 0.7, 3.0, 0.7])
    header[0].markdown("**Del**")
    header[1].markdown("**Scheduled**")
    header[2].markdown("**Player**")
    header[3].markdown("**Instance**")
    header[4].markdown("**Task type**")
    header[5].markdown("**Region**")
    header[6].markdown("**Pri**")
    header[7].markdown("**Coop**")
    header[8].markdown("**Task ID**")
    header[9].markdown("**Copy**")

    selected_ids: list[str] = []
    for idx, r in enumerate(rows):
        overdue = r.scheduled_at < now
        rel = _rel_time(r.scheduled_at, now)
        scheduled_disp = f"⚠️ {rel}" if overdue else rel
        k = f"qrow_{idx}_{r.task_id}"

        cols = st.columns([0.55, 1.1, 1.0, 1.0, 1.4, 1.0, 0.7, 0.7, 3.0, 0.7])
        if cols[0].checkbox("del", value=False, key=f"{k}_del", label_visibility="collapsed"):
            selected_ids.append(r.task_id)
        cols[1].write(scheduled_disp)
        cols[2].write(r.player_id)
        cols[3].write(r.instance_id)
        cols[4].write(r.task_type)
        cols[5].write(r.region or "")
        cols[6].write(str(r.priority))
        cols[7].checkbox(
            "coop",
            value=bool(r.cooperative),
            disabled=True,
            key=f"{k}_coop",
            label_visibility="collapsed",
        )
        cols[8].write(r.task_id)
        if cols[9].button("📋", key=f"{k}_copy", help="Copy row JSON"):
            payload = r.payload or {}
            txt = json.dumps(payload, ensure_ascii=False, indent=2)
            st.session_state["queue_copy_json"] = txt
            st.toast("JSON ready to copy (open expander above).")
            st.rerun()

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

    # History — shown at the bottom so current queue stays at top.
    if inst_ids:
        st.divider()
        with st.expander("Recent executions (last 20 per instance)", expanded=False):
            history_by_instance = {
                iid: fetch_queue_history_rows(client, instance_id=iid, limit=20)
                for iid in inst_ids
            }
            all_hist_scenarios = sorted({
                h.scenario or h.task_type
                for hist in history_by_instance.values()
                for h in hist
                if h.scenario or h.task_type
            })
            hc1, hc2 = st.columns([1, 3], vertical_alignment="bottom")
            with hc1:
                hist_only_failed = st.checkbox(
                    "Only failed",
                    value=False,
                    key="queue_history_only_failed",
                )
            with hc2:
                hist_scenarios = st.multiselect(
                    "Scenario",
                    all_hist_scenarios,
                    placeholder="All scenarios",
                    key="queue_history_scenarios",
                )
            tabs = st.tabs(inst_ids) if len(inst_ids) > 1 else None
            containers = tabs if tabs is not None else [st.container()]
            for tab, iid in zip(containers, inst_ids, strict=False):
                with tab:
                    hist = history_by_instance.get(iid, [])
                    if hist_only_failed:
                        hist = [h for h in hist if not h.success]
                    if hist_scenarios:
                        hist = [
                            h for h in hist
                            if (h.scenario or h.task_type) in hist_scenarios
                        ]
                    if not hist:
                        st.caption("No completed tasks match the current filter.")
                        continue
                    hist_data = []
                    for h in hist:
                        finished_str = (
                            time.strftime("%H:%M:%S", time.localtime(h.finished_at))
                            if h.finished_at
                            else "—"
                        )
                        detail = h.reason or h.error or h.task_id
                        if len(detail) > 64:
                            detail = f"{detail[:61]}..."
                        hist_data.append({
                            "Finished": finished_str,
                            "Scenario": h.scenario or h.task_type,
                            "Player": h.player_id or "—",
                            "Region": h.region or "",
                            "Dur": f"{h.duration_s:.1f}s",
                            "Status": "✅ ok" if h.success else "❌ failed",
                            "Reason / task": detail,
                        })
                    df_hist = pd.DataFrame(hist_data)
                    event = st.dataframe(
                        df_hist,
                        use_container_width=True,
                        hide_index=True,
                        selection_mode="single-row",
                        on_select="rerun",
                        column_config={
                            "Status": st.column_config.TextColumn(width="small"),
                            "Dur": st.column_config.TextColumn(width="small"),
                            "Finished": st.column_config.TextColumn(width="small"),
                        },
                    )
                    sel = event.selection.get("rows", [])
                    if sel:
                        h_sel = hist[sel[0]]
                        if h_sel.payload:
                            st.code(
                                json.dumps(h_sel.payload, ensure_ascii=False, indent=2),
                                language="json",
                            )
                        else:
                            st.caption("No payload.")


_queue_fragment()

st.divider()
if st.button("Run optimizer now (scheduler)"):
    push_scheduler_command(client, {"cmd": "optimize_now"})
    st.success("optimize_now sent to scheduler channel.")
