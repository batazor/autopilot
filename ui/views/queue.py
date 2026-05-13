"""Priority queue viewer and scheduler nudge."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import streamlit as st
import yaml

from config.loader import load_settings
from config.reference_naming import event_icon_abs_path
from scenarios.dsl_schema import resolve_dsl_scenario_yaml_path
from ui.redis_client import (
    QueueHistoryRow,
    clear_queue_tasks,
    fetch_queue_history_rows,
    fetch_queue_rows,
    fetch_running_queue_row,
    get_instance_state,
    push_scheduler_command,
    remove_queue_task,
    require_redis_connection,
    run_queue_task_now,
)
from ui.views._debug_scenarios_progress import _load_scenario_step_summaries

_REPO = Path(__file__).resolve().parents[2]


@st.cache_data(ttl=10)
def _scenario_icon_path(repo_str: str, scenario_key: str) -> str | None:
    """Resolve scenario ``icon:`` slug → absolute icon path string (or ``None``)."""
    if not scenario_key:
        return None
    repo = Path(repo_str)
    path = resolve_dsl_scenario_yaml_path(repo, scenario_key)
    if path is None or not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None
    slug = str(raw.get("icon") or "") if isinstance(raw, dict) else ""
    icon = event_icon_abs_path(repo, slug)
    return str(icon) if icon is not None else None


def _history_steps_summary(h: QueueHistoryRow) -> str:
    """Compact DSL step progress for the history table."""
    total = h.steps_total
    trace = h.steps_trace
    done_full = h.scenario_completed
    if total is None and not trace:
        return "—"
    n = len(trace) if trace else 0
    mid = f"{n}/{total}" if total is not None else str(n)
    if done_full is True:
        return f"{mid} · complete"
    if done_full is False:
        return f"{mid} · partial"
    return mid


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

    hdr, clear_btn, refresh_btn = st.columns([4, 1, 1])
    with hdr:
        inst_ids = [i.instance_id for i in settings.instances]
        running_rows = [
            (iid, fetch_running_queue_row(client, instance_id=iid)) for iid in inst_ids
        ]
        running_rows = [(iid, r) for iid, r in running_rows if r is not None and r.task_id]
        if running_rows:
            with st.expander("Running now (all instances)", expanded=True):
                for iid, r in running_rows:
                    inst_state = get_instance_state(client, iid)
                    dur = ""
                    if r.started_at > 0:
                        dur = f" · {_rel_time(r.started_at, now)}"
                    st.info(
                        f"**{iid}** · **{r.task_type}** · task_id `{r.task_id}` · "
                        f"player `{r.player_id or '—'}`"
                        + (f" · region `{r.region}`" if r.region else "")
                        + dur
                    )
                    active_scenario = str(inst_state.get("current_scenario") or "").strip()
                    summaries = (
                        _load_scenario_step_summaries(_REPO, active_scenario)
                        if active_scenario
                        else ()
                    )
                    total_steps = len(summaries)
                    try:
                        step_now = int(inst_state.get("last_active_scenario_step") or 0)
                    except (TypeError, ValueError):
                        step_now = 0
                    step_display = max(0, min(step_now, total_steps)) if total_steps else 0
                    ratio = step_display / total_steps if total_steps else 0.0
                    if total_steps > 0:
                        bar_text = f"{active_scenario} · Step {step_display}/{total_steps}"
                    elif active_scenario:
                        bar_text = f"{active_scenario} · running"
                    else:
                        bar_text = f"{r.task_type} · running"
                    icon_path = _scenario_icon_path(str(_REPO), active_scenario)
                    if icon_path:
                        icon_col, bar_col = st.columns([1, 11])
                        with icon_col:
                            st.image(icon_path, width=48)
                        with bar_col:
                            st.progress(min(1.0, max(0.0, ratio)), text=bar_text)
                    else:
                        st.progress(min(1.0, max(0.0, ratio)), text=bar_text)
        if rows:
            overdue_n = sum(1 for r in rows if r.scheduled_at < now)
            parts = [f"**{len(rows)}** task(s)"]
            if overdue_n:
                parts.append(f"**{overdue_n}** overdue")
            st.caption(" · ".join(parts) + " · refreshes every 3 s")
    with clear_btn:
        if st.button(
            "Clear queue",
            help="Drop all pending tasks across instances (history is preserved).",
            key="queue_clear_btn",
            type="primary",
            disabled=not rows,
            width="stretch",
        ):
            removed = clear_queue_tasks(client)
            if removed:
                st.toast(f"Cleared {removed} pending task(s).")
            else:
                st.toast("Queue was already empty.")
            st.rerun()
    with refresh_btn:
        if st.button("🔄", help="Refresh now", key="queue_refresh_btn", width="stretch"):
            st.rerun()

    inst_ids = [i.instance_id for i in settings.instances]

    if not rows:
        st.info("Queue is empty.")

    if rows:
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
        else:
            st.markdown("**Queue items**")
            _COL_WIDTHS = [0.55, 0.55, 1.1, 1.0, 1.0, 1.4, 1.0, 0.7, 0.7, 3.0, 0.7]
            header = st.columns(_COL_WIDTHS)
            header[0].markdown("**Del**")
            header[1].markdown("**Run**")
            header[2].markdown("**Scheduled**")
            header[3].markdown("**Player**")
            header[4].markdown("**Instance**")
            header[5].markdown("**Task type**")
            header[6].markdown("**Region**")
            header[7].markdown("**Pri**")
            header[8].markdown("**Coop**")
            header[9].markdown("**Task ID**")
            header[10].markdown("**Copy**")

            selected_ids: list[str] = []
            for idx, r in enumerate(rows):
                overdue = r.scheduled_at < now
                rel = _rel_time(r.scheduled_at, now)
                scheduled_disp = f"⚠️ {rel}" if overdue else rel
                k = f"qrow_{idx}_{r.task_id}"

                cols = st.columns(_COL_WIDTHS)
                if cols[0].checkbox("del", value=False, key=f"{k}_del", label_visibility="collapsed"):
                    selected_ids.append(r.task_id)
                if cols[1].button(
                    "▶️",
                    key=f"{k}_run",
                    help="Re-score this task to now and nudge the scheduler.",
                ):
                    if run_queue_task_now(client, r.task_id):
                        push_scheduler_command(client, {"cmd": "optimize_now"})
                        st.toast(f"Scheduled {r.task_type} to run now.")
                    else:
                        st.toast("Task not found — it may have already been popped.")
                    st.rerun()
                cols[2].write(scheduled_disp)
                cols[3].write(r.player_id)
                cols[4].write(r.instance_id)
                cols[5].write(r.task_type)
                cols[6].write(r.region or "")
                cols[7].write(str(r.priority))
                cols[8].checkbox(
                    "coop",
                    value=bool(r.cooperative),
                    disabled=True,
                    key=f"{k}_coop",
                    label_visibility="collapsed",
                )
                cols[9].write(r.task_id)
                if cols[10].button("📋", key=f"{k}_copy", help="Copy row JSON"):
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
    _HISTORY_LIMIT = 50
    if inst_ids:
        st.divider()
        st.subheader("Execution history")
        history_by_instance = {
            iid: fetch_queue_history_rows(client, instance_id=iid, limit=_HISTORY_LIMIT)
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
                for seq, h in enumerate(hist, start=1):
                    started_str = (
                        time.strftime("%H:%M:%S", time.localtime(h.started_at))
                        if h.started_at
                        else "—"
                    )
                    finished_str = (
                        time.strftime("%H:%M:%S", time.localtime(h.finished_at))
                        if h.finished_at
                        else "—"
                    )
                    detail = h.reason or h.error or h.task_id
                    if len(detail) > 64:
                        detail = f"{detail[:61]}..."
                    hist_data.append({
                        "#": seq,
                        "Started": started_str,
                        "Finished": finished_str,
                        "Scenario": h.scenario or h.task_type,
                        "Player": h.player_id or "—",
                        "Region": h.region or "",
                        "Dur": f"{h.duration_s:.1f}s",
                        "Status": "✅" if h.success else "❌",
                        "Steps": _history_steps_summary(h),
                        "Reason / task": detail,
                    })
                df_hist = pd.DataFrame(hist_data)
                event = st.dataframe(
                    df_hist,
                    width="stretch",
                    hide_index=True,
                    selection_mode="single-row",
                    on_select="rerun",
                    column_config={
                        "#": st.column_config.NumberColumn(width="small"),
                        "Status": st.column_config.TextColumn(width="small"),
                        "Dur": st.column_config.TextColumn(width="small"),
                        "Started": st.column_config.TextColumn(width="small"),
                        "Finished": st.column_config.TextColumn(width="small"),
                        "Steps": st.column_config.TextColumn(width="medium"),
                    },
                )
                sel = event.selection.get("rows", [])
                if sel:
                    h_sel = hist[sel[0]]
                    # Jump-to-debug: pre-fills the Debug page with this scenario +
                    # player so the user lands directly on the right context.
                    debug_scenario = h_sel.scenario or h_sel.task_type
                    if debug_scenario:
                        debug_params: dict[str, str] = {"scenario": debug_scenario}
                        if h_sel.player_id:
                            debug_params["player"] = h_sel.player_id
                        st.link_button(
                            f"🔍 Debug `{debug_scenario}`"
                            + (f" · player `{h_sel.player_id}`" if h_sel.player_id else ""),
                            f"/debug_scenarios?{urlencode(debug_params)}",
                            help="Open the Debug page with this scenario and player pre-selected.",
                        )
                    if h_sel.steps_trace:
                        with st.expander("DSL step trace", expanded=False):
                            trace_df = pd.DataFrame(h_sel.steps_trace)
                            if "i" in trace_df.columns:
                                # `i` mixes ints (outer step index) and strings
                                # like "6.0" (nested iter paths); arrow needs one dtype.
                                trace_df["i"] = trace_df["i"].astype(str)
                            st.dataframe(
                                trace_df,
                                hide_index=True,
                                width="stretch",
                            )
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
