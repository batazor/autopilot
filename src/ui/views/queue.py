"""Priority queue viewer and scheduler nudge (nested-table UI)."""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode, urlparse, urlunparse

import pandas as pd
import streamlit as st
from streamlit_nested_table import TableColumn, nested_table, table_column

from config.loader import load_settings
from config.reference_naming import event_icon_abs_path
from dsl import template_resolver as _tmpl
from ui.redis_client import (
    QueueHistoryRow,
    QueueRow,
    RunningQueueRow,
    clear_queue_tasks,
    fetch_queue_explain_rows,
    fetch_queue_history_rows,
    fetch_queue_rows,
    fetch_running_queue_row,
    get_instance_state,
    push_scheduler_command,
    remove_queue_task,
    require_redis_connection,
    run_queue_task_now,
    sort_queue_rows_by_execution_order,
)
from ui.views._debug_scenarios_progress import _load_scenario_step_summaries

if TYPE_CHECKING:
    import redis

_REPO = Path(__file__).resolve().parents[2]
_DEVICE_LABEL = "(device)"


@st.cache_data(ttl=10)
def _scenario_icon_path(repo_str: str, scenario_key: str) -> str | None:
    if not scenario_key:
        return None
    repo = Path(repo_str)
    loaded = _tmpl.load_doc(repo, scenario_key)
    if loaded is None:
        return None
    _path, raw = loaded
    slug = str(raw.get("icon") or "") if isinstance(raw, dict) else ""
    icon = event_icon_abs_path(repo, slug)
    return str(icon) if icon is not None else None


@st.cache_data(ttl=10)
def _scenario_display_label(repo_str: str, scenario_key: str) -> str:
    return _tmpl.display_name(Path(repo_str), scenario_key)


def _internal_page_url(page: str, query: dict[str, str] | None = None) -> str:
    raw = getattr(st.context, "url", None)
    if not (raw and str(raw).strip()):
        raw = "http://localhost:8501/"
    u = urlparse(str(raw))
    parts = [p for p in u.path.strip("/").split("/") if p]
    if parts:
        parts[-1] = page
        path = "/" + "/".join(parts)
    else:
        path = "/" + page
    q = urlencode(query or {})
    return urlunparse((u.scheme, u.netloc, path, "", q, ""))


def _tempo_trace_url(trace_id: str) -> str:
    tid = str(trace_id or "").strip()
    if not tid:
        return ""
    template = (
        os.environ.get("WOS_TEMPO_TRACE_URL_TEMPLATE")
        or os.environ.get("GRAFANA_TEMPO_TRACE_URL_TEMPLATE")
        or ""
    ).strip()
    if template:
        return template.replace("{trace_id}", quote(tid, safe=""))
    return ""


def _history_steps_summary(h: QueueHistoryRow) -> str:
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


def _nested_table_height(n: int, *, cap: int = 520) -> int:
    return min(48 + max(n, 1) * 34, cap)


def _player_label(player_id: str) -> str:
    return player_id or _DEVICE_LABEL


def _pending_nested_columns() -> list[TableColumn]:
    """Column defs for queue plugin (Tailwind pills + links)."""
    return [
        table_column(
            "scheduled",
            "Scheduled",
            width=172,
            cell_type="pill",
            pill_preset="scheduled",
        ),
        table_column("player", "Player", width=100),
        table_column("instance", "Instance", width=110),
        table_column("scenario", "Scenario", width=260),
        table_column("key", "Key", width=220),
        table_column("region", "Region", width=140),
        table_column("priority", "Pri", width=72, align="right"),
        table_column("coop", "Coop", width=92, cell_type="pill", pill_preset="coop"),
        table_column("task_id", "Task ID", width=260),
        table_column(
            "debug",
            "→",
            width=92,
            cell_type="link",
            link_text_key="debug_label",
        ),
        table_column(
            "instance_open",
            "↗",
            width=88,
            cell_type="link",
            link_text_key="instance_open_label",
        ),
    ]


def _build_pending_nested_rows(rows: list[QueueRow], now: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        overdue = r.scheduled_at < now
        rel = _rel_time(r.scheduled_at, now)
        scheduled = f"overdue · {rel}" if overdue else rel
        scen = _scenario_display_label(str(_REPO), r.task_type)
        debug_q: dict[str, str] = {"scenario": r.task_type}
        if r.player_id:
            debug_q["player"] = r.player_id
        out.append(
            {
                "id": r.task_id,
                "scheduled": scheduled,
                "player": _player_label(r.player_id),
                "instance": r.instance_id,
                "scenario": scen,
                "key": r.task_type,
                "region": r.region or "—",
                "priority": int(r.priority),
                "coop": "yes" if r.cooperative else "no",
                "task_id": r.task_id,
                "debug": _internal_page_url("debug_scenarios", debug_q),
                "debug_label": "Debug",
                "instance_open": _internal_page_url(
                    "instance", {"instance_id": r.instance_id}
                ),
                "instance_open_label": "Inst",
            }
        )
    return out


def _task_pick_label(r: QueueRow) -> str:
    scen = _scenario_display_label(str(_REPO), r.task_type)
    return f"{scen} · {r.instance_id} · {r.task_id[:12]}…"


def _render_clipboard_button(label: str, text: str) -> None:
    """Render payload access without deprecated raw-HTML components."""
    st.download_button(
        label=label.replace("Copy", "Download"),
        data=text,
        file_name="queue_payload.json",
        mime="application/json",
        width="stretch",
    )


def _render_queue_actions(
    client: redis.Redis,
    rows: list[QueueRow],
    selected_task_ids: set[str],
) -> None:
    if not rows:
        return

    st.caption("Queue actions")
    tid_order = [r.task_id for r in rows]
    preferred = next((t for t in tid_order if t in selected_task_ids), None)
    default_idx = tid_order.index(preferred) if preferred else 0

    a1, a2, a3, a4 = st.columns([3.2, 1, 1, 1], vertical_alignment="bottom")
    with a1:
        pick_id = st.selectbox(
            "Task",
            options=tid_order,
            index=min(default_idx, len(tid_order) - 1),
            format_func=lambda tid: _task_pick_label(next(r for r in rows if r.task_id == tid)),
            key="queue_action_pick",
            label_visibility="collapsed",
        )
    pick = next(r for r in rows if r.task_id == pick_id)

    with a2:
        if st.button("Run now", key="queue_action_run", width="stretch"):
            if run_queue_task_now(client, pick.task_id):
                push_scheduler_command(client, {"cmd": "optimize_now"})
                st.toast(f"Scheduled `{pick.task_type}` to run now.")
            else:
                st.toast("Task not found — it may have already been popped.")
            st.rerun()
    with a3:
        n_sel = len(selected_task_ids)
        del_label = f"Delete ({n_sel})" if n_sel else "Delete selected"
        if st.button(
            del_label,
            key="queue_action_delete",
            type="primary" if n_sel else "secondary",
            disabled=not n_sel,
            width="stretch",
        ):
            removed = sum(
                1 for r in rows if r.task_id in selected_task_ids and remove_queue_task(client, r.task_id)
            )
            if removed:
                st.success(f"Removed {removed} task(s).")
            else:
                st.warning("None found — tasks may have already been processed.")
            st.rerun()
    with a4:
        payload = pick.payload or {}
        payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
        _render_clipboard_button("Copy JSON", payload_json)


def _running_nested_columns() -> list[TableColumn]:
    return [
        table_column("instance", "Instance", width=118),
        table_column("scenario", "Scenario", width=248),
        table_column("key", "Key", width=218),
        table_column("player", "Player", width=116),
        table_column("region", "Region", width=146),
        table_column("started", "Started", width=146),
        table_column("task_id", "Task ID", width=252),
        table_column(
            "instance_open",
            "↗",
            width=94,
            cell_type="link",
            link_text_key="instance_open_label",
        ),
    ]


def _render_running_progress(
    iid: str,
    r: RunningQueueRow,
    inst_state: dict[str, str],
    now: float,
) -> None:
    task_type_label = _scenario_display_label(str(_REPO), r.task_type)
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
    busy_running = bool(
        active_scenario
        and str(inst_state.get("state") or "").strip().lower() == "busy"
        and str(inst_state.get("current_task_id") or "").strip()
    )
    cap = (total_steps - 1) if busy_running and total_steps else total_steps
    step_display = max(0, min(step_now, cap)) if total_steps else 0
    nav_target = str(inst_state.get("nav_target") or "").strip()
    from ui.scenario_progress_metrics import (
        compute_scenario_progress_metrics,
        format_scenario_progress_label,
    )

    metrics = compute_scenario_progress_metrics(
        step_current=step_display,
        step_total=total_steps,
        is_running=busy_running,
        nav_target=nav_target,
    )
    ratio = float(metrics["progress_ratio"])
    active_label = (
        _scenario_display_label(str(_REPO), active_scenario) if active_scenario else ""
    )
    if active_scenario and total_steps > 0:
        bar_text = format_scenario_progress_label(
            scenario_label=active_label,
            scenario_key=active_scenario,
            step_current=step_display,
            step_total=total_steps,
            step_iter=int(inst_state.get("last_active_scenario_iter") or 0),
            is_running=busy_running,
            is_navigating=bool(metrics["is_navigating"]),
            nav_target=nav_target,
        )
    elif active_scenario:
        bar_text = f"{active_label} · running"
    else:
        bar_text = f"{task_type_label} · running"
    dur = ""
    if r.started_at > 0:
        dur = _rel_time(r.started_at, now)
    st.caption(f"`{iid}` · {bar_text}" + (f" · {dur}" if dur else ""))
    icon_path = _scenario_icon_path(str(_REPO), active_scenario or r.task_type)
    if icon_path:
        icon_col, bar_col = st.columns([1, 11])
        with icon_col:
            st.image(icon_path, width=44)
        with bar_col:
            st.progress(min(1.0, max(0.0, ratio)), text=bar_text)
    else:
        st.progress(min(1.0, max(0.0, ratio)), text=bar_text)


def _render_running_section(
    client: redis.Redis,
    inst_ids: list[str],
    now: float,
) -> None:
    running_rows = [
        (iid, fetch_running_queue_row(client, instance_id=iid)) for iid in inst_ids
    ]
    running_rows = [
        (iid, r) for iid, r in running_rows if r is not None and r.task_id
    ]
    if not running_rows:
        return

    st.subheader("Running now")
    table_rows: list[dict[str, Any]] = []
    for iid, r in running_rows:
        dur = _rel_time(r.started_at, now) if r.started_at > 0 else "—"
        scen = _scenario_display_label(str(_REPO), r.task_type)
        table_rows.append(
            {
                "id": f"{iid}:{r.task_id}",
                "instance": iid,
                "scenario": scen,
                "key": r.task_type,
                "player": r.player_id or "—",
                "region": r.region or "—",
                "started": dur,
                "task_id": r.task_id,
                "instance_open": _internal_page_url("instance", {"instance_id": iid}),
                "instance_open_label": "Open",
            }
        )
    nested_table(
        table_rows,
        _running_nested_columns(),
        height=_nested_table_height(len(table_rows), cap=360),
        striped=True,
        compact=True,
        hide_expand=True,
        key="queue_running_nested",
    )
    for iid, r in running_rows:
        inst_state = get_instance_state(client, iid)
        with st.expander(f"Progress · {iid}", expanded=len(running_rows) == 1):
            _render_running_progress(iid, r, inst_state, now)


def _explain_nested_columns() -> list[TableColumn]:
    return [
        table_column("#", "#", width=72, align="center", cell_type="pill", pill_preset="rank_indicator"),
        table_column("scenario", "Scenario", width=246),
        table_column("key", "Key", width=218),
        table_column("player", "Player", width=118),
        table_column("base", "Base", width=92, align="right"),
        table_column("effective", "Effective", width=96, align="right"),
        table_column("graph", "Graph−", width=90, align="right"),
        table_column("recent", "Recent−", width=98, align="right"),
        table_column("hops", "Hops", width=84),
        table_column(
            "reachable",
            "Reach",
            width=106,
            cell_type="pill",
            pill_preset="reachable",
        ),
        table_column("required", "Required", width=192),
        table_column("recent_runs", "Runs", width=84, align="right"),
        table_column(
            "scheduled",
            "Due",
            width=136,
            cell_type="pill",
            pill_preset="scheduled",
        ),
        table_column("task_id", "Task ID", width=286),
    ]


def _history_nested_columns() -> list[TableColumn]:
    return [
        table_column("#", "#", width=60, align="center"),
        table_column(
            "status",
            "Status",
            width=108,
            cell_type="pill",
            pill_preset="history_status",
        ),
        table_column("duration", "Dur", width=92),
        table_column("started", "Started", width=118),
        table_column("finished", "Finished", width=118),
        table_column("steps", "Steps", width=168),
        table_column("scenario", "Scenario", width=266),
        table_column("key", "Key", width=226),
        table_column("player", "Player", width=134),
        table_column("trace_id_short", "Trace", width=112),
        table_column("region", "Region", width=174),
        table_column("detail", "Reason / task", width=356),
        table_column(
            "tempo",
            "Tempo",
            width=104,
            cell_type="link",
            link_text_key="tempo_label",
        ),
        table_column(
            "debug",
            "→",
            width=104,
            cell_type="link",
            link_text_key="debug_label",
        ),
    ]


st.title("Task queue")
st.caption(
    "Pending tasks per instance (Redis ZSET). Select rows with checkboxes; "
    "use **Queue actions** for run-now and JSON copy. Refreshes every 3 s."
)

client = require_redis_connection()


@st.fragment(run_every=timedelta(seconds=3))
def _queue_fragment() -> None:
    now = time.time()
    rows = sort_queue_rows_by_execution_order(client, fetch_queue_rows(client))
    settings = load_settings()
    inst_ids = [i.instance_id for i in settings.instances]

    toolbar = st.columns([4, 1, 1])
    with toolbar[0]:
        overdue_n = sum(1 for r in rows if r.scheduled_at < now)
        running_n = sum(
            1
            for iid in inst_ids
            if (r := fetch_running_queue_row(client, instance_id=iid)) and r.task_id
        )
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Pending", len(rows))
        m2.metric("Overdue", overdue_n)
        m3.metric("Running", running_n)
        m4.metric("Instances", len(inst_ids))
    with toolbar[1]:
        if st.button(
            "Clear queue",
            help="Drop all pending tasks (history preserved).",
            key="queue_clear_btn",
            type="primary",
            disabled=not rows,
            width="stretch",
        ):
            removed = clear_queue_tasks(client)
            st.toast(
                f"Cleared {removed} pending task(s)."
                if removed
                else "Queue was already empty."
            )
            st.rerun()
    with toolbar[2]:
        if st.button("Refresh", key="queue_refresh_btn", width="stretch"):
            st.rerun()

    _render_running_section(client, inst_ids, now)

    if rows:
        st.divider()
        st.subheader("Pending queue")

        all_players = sorted({_player_label(r.player_id) for r in rows})
        all_instances = sorted({r.instance_id for r in rows if r.instance_id})

        fc1, fc2 = st.columns(2)
        with fc1:
            sel_players = st.multiselect(
                "Player",
                all_players,
                placeholder="All players",
                key="queue_filter_player",
            )
        with fc2:
            sel_instances = st.multiselect(
                "Instance",
                all_instances,
                placeholder="All instances",
                key="queue_filter_instance",
            )

        filtered = list(rows)
        if sel_players:
            filtered = [
                r
                for r in filtered
                if _player_label(r.player_id) in sel_players
            ]
        if sel_instances:
            filtered = [r for r in filtered if r.instance_id in sel_instances]

        if not filtered:
            st.info("No tasks match the current filter.")
        else:
            st.caption(
                "Tables use the **nested-table** component (TanStack + Tailwind) "
                "for striping and status chips consistent with Wiki / Scenarios."
            )

            pending_ids_now = {r.task_id for r in filtered}
            st.session_state.setdefault("queue_pending_selected_ids", [])
            valid_sel = [
                x
                for x in st.session_state["queue_pending_selected_ids"]
                if x in pending_ids_now
            ]
            st.session_state["queue_pending_selected_ids"] = valid_sel

            pending_payload = _build_pending_nested_rows(filtered, now)
            sel_nt = nested_table(
                pending_payload,
                _pending_nested_columns(),
                height=_nested_table_height(len(filtered)),
                striped=True,
                compact=True,
                multi_select=True,
                selected_ids=st.session_state["queue_pending_selected_ids"],
                get_row_id="id",
                hide_expand=True,
                key="queue_pending_nested_table",
            )
            if isinstance(sel_nt, dict):
                raw_ids = sel_nt.get("selectedIds")
                if isinstance(raw_ids, list):
                    st.session_state["queue_pending_selected_ids"] = [
                        str(x) for x in raw_ids if str(x) in pending_ids_now
                    ]

            selected_task_ids = set(st.session_state["queue_pending_selected_ids"])
            _render_queue_actions(client, filtered, selected_task_ids)
    else:
        st.info("Queue is empty.")

    if inst_ids:
        st.divider()
        st.subheader(
            "Why this order?",
            help=(
                "Top-10 ranked due candidates per instance. "
                "Smallest sort key wins the next pop_due claim."
            ),
        )
        explain_tabs = st.tabs(inst_ids) if len(inst_ids) > 1 else None
        explain_containers = (
            explain_tabs if explain_tabs is not None else [st.container()]
        )
        for tab, iid in zip(explain_containers, inst_ids, strict=False):
            with tab:
                inst_state = get_instance_state(client, iid) or {}
                screen = str(inst_state.get("current_screen") or "").strip()
                ap = str(inst_state.get("active_player") or "").strip()
                explain_rows_raw = fetch_queue_explain_rows(
                    instance_id=iid, current_screen=screen, n=10
                )
                st.caption(
                    f"`current_screen`: {screen or '—'} · "
                    f"`active_player`: {ap or '—'}"
                )
                if not explain_rows_raw:
                    st.info("No due candidates ranked for this instance.")
                    continue
                explain_data: list[dict[str, Any]] = []
                for seq, er in enumerate(explain_rows_raw, start=1):
                    key = str(er.get("task_type") or "")
                    explain_data.append(
                        {
                            "id": f"{iid}-e-{seq}-{er.get('task_id') or key}",
                            "#": str(seq),
                            "scenario": _scenario_display_label(str(_REPO), key),
                            "key": key,
                            "player": str(er.get("player_id") or "") or "—",
                            "base": int(er.get("base_priority") or 0),
                            "effective": int(er.get("effective_priority") or 0),
                            "graph": int(er.get("graph_debuff") or 0),
                            "recent": int(er.get("recent_debuff") or 0),
                            "hops": (
                                "∞"
                                if not bool(er.get("reachable"))
                                else str(int(er.get("hops") or 0))
                            ),
                            "reachable": "yes" if bool(er.get("reachable")) else "no",
                            "required": str(er.get("required_node") or "") or "—",
                            "recent_runs": int(er.get("recent_count") or 0),
                            "scheduled": _rel_time(float(er.get("run_at") or now), now),
                            "task_id": str(er.get("task_id") or ""),
                        }
                    )
                nested_table(
                    explain_data,
                    _explain_nested_columns(),
                    height=_nested_table_height(len(explain_data), cap=440),
                    striped=True,
                    compact=True,
                    hide_expand=True,
                    key=f"queue_explain_{iid}",
                )

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
                        h
                        for h in hist
                        if (h.scenario or h.task_type) in hist_scenarios
                    ]
                if not hist:
                    st.caption("No completed tasks match the current filter.")
                    continue
                hist_data: list[dict[str, Any]] = []
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
                        detail = f"{detail[:61]}…"
                    scen_key = h.scenario or h.task_type
                    debug_q: dict[str, str] = {"scenario": scen_key}
                    if h.player_id:
                        debug_q["player"] = h.player_id
                    trace_id = str(h.trace_id or "").strip()
                    tempo_url = _tempo_trace_url(trace_id)
                    hist_data.append(
                        {
                            "id": f"h-{iid}-{seq}",
                            "hist_idx": seq - 1,
                            "#": str(seq),
                            "started": started_str,
                            "finished": finished_str,
                            "scenario": _scenario_display_label(str(_REPO), scen_key),
                            "key": scen_key,
                            "player": h.player_id or "—",
                            "trace_id_short": trace_id[:12] if trace_id else "—",
                            "region": h.region or "—",
                            "duration": f"{h.duration_s:.1f}s",
                            "status": "done" if h.success else "failed",
                            "steps": _history_steps_summary(h),
                            "detail": detail,
                            "tempo": tempo_url,
                            "tempo_label": "Open" if tempo_url else "",
                            "debug": _internal_page_url("debug_scenarios", debug_q),
                            "debug_label": "Debug",
                        }
                    )
                hist_sel = nested_table(
                    hist_data,
                    _history_nested_columns(),
                    height=_nested_table_height(len(hist_data), cap=460),
                    striped=True,
                    compact=True,
                    selectable=True,
                    hide_expand=True,
                    key=f"queue_hist_sel_{iid}",
                )

                hist_idx: int | None = None
                if isinstance(hist_sel, dict):
                    row_payload = hist_sel.get("row")
                    if isinstance(row_payload, dict):
                        ix = row_payload.get("hist_idx")
                        if isinstance(ix, (int, float)) and not isinstance(ix, bool):
                            hist_idx = int(ix)

                if hist_idx is not None and 0 <= hist_idx < len(hist):
                    h_sel = hist[hist_idx]
                    if h_sel.trace_id:
                        trace_cols = st.columns([2, 1], vertical_alignment="bottom")
                        with trace_cols[0]:
                            st.caption("Trace ID")
                            st.code(h_sel.trace_id, language=None)
                        tempo_url = _tempo_trace_url(h_sel.trace_id)
                        if tempo_url:
                            with trace_cols[1]:
                                st.link_button("Open in Tempo", tempo_url, width="stretch")
                    if h_sel.steps_trace:
                        with st.expander("DSL step trace", expanded=False):
                            trace_df = pd.DataFrame(h_sel.steps_trace)
                            if "i" in trace_df.columns:
                                trace_df["i"] = trace_df["i"].astype(str)
                            st.dataframe(trace_df, hide_index=True, width="stretch")
                    if h_sel.payload:
                        with st.expander("Task payload", expanded=False):
                            st.code(
                                json.dumps(h_sel.payload, ensure_ascii=False, indent=2),
                                language="json",
                            )


_queue_fragment()

st.divider()
if st.button("Run optimizer now (scheduler)"):
    push_scheduler_command(client, {"cmd": "optimize_now"})
    st.success("optimize_now sent to scheduler channel.")
