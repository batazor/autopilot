"""Per-instance / per-task debug timeline.

Reads the bounded LIST at ``wos:debug:timeline:<instance_id>`` populated by
producers across the codebase (queue, worker, overlay, approval gates, DSL
runtime). The view exists to collapse what used to live in 4 separate
sources — ``wos:queue:history``, ``wos:ui:notifications``,
``wos:instance:*:state`` and stdout logs — into one chronological stream a
debugger can scroll once.

Filter by ``task_id`` to see the lifecycle of a single task (overlay match
→ enqueue → pop → DSL steps → approval gates → terminal event). Empty
``task_id`` shows the raw stream for the whole instance.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from config.loader import load_settings
from debug.timeline import EVENT_TYPES, read_timeline
from ui.redis_client import require_redis_connection


_DEFAULT_LIMIT = 500


def _instance_ids(settings: Any) -> list[str]:
    return [inst.instance_id for inst in settings.instances]


def _fmt_ts(ts: float) -> str:
    """``HH:MM:SS.mmm`` — debug-grade short stamp, no timezone clutter."""
    try:
        dt = datetime.fromtimestamp(float(ts))
    except (TypeError, ValueError, OSError):
        return ""
    return dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"


def _row_summary(row: dict[str, Any]) -> str:
    """Short, human-readable one-liner. Surfaces high-signal fields per event."""
    event = str(row.get("event") or "")
    parts: list[str] = []
    tt = str(row.get("task_type") or "")
    if tt:
        parts.append(tt)
    region = row.get("region") or row.get("source_region")
    if region:
        parts.append(f"@{region}")
    if event == "queue.popped":
        ep = row.get("effective_priority")
        pr = row.get("priority")
        if ep is not None and pr is not None and ep != pr:
            parts.append(f"prio={pr}→{ep}")
        elif pr is not None:
            parts.append(f"prio={pr}")
    elif event in ("task.finished", "task.failed", "task.preempted"):
        dur = row.get("duration_s")
        if dur is not None:
            parts.append(f"{float(dur):.2f}s")
        if row.get("reason"):
            parts.append(f"reason={row['reason']}")
        if event == "task.preempted" and row.get("preempted_by"):
            parts.append(f"by={row['preempted_by']}")
    elif event == "overlay.throttled":
        ttl = row.get("ttl_s")
        if ttl:
            parts.append(f"ttl={ttl}s")
    elif event == "dsl.step":
        summary = row.get("summary") or row.get("step_index")
        if summary:
            parts.append(f"step:{summary}")
        if row.get("status"):
            parts.append(str(row["status"]))
    elif event == "approval.requested":
        if row.get("payload_type"):
            parts.append(str(row["payload_type"]))
    return " · ".join(parts)


def main() -> None:
    st.title("Debug timeline")
    st.caption(
        "Per-instance event stream: overlay matches, queue scheduling, task "
        "lifecycle, approvals, DSL steps. Filter by `task_id` to collapse to "
        "one task's chain. Backed by `wos:debug:timeline:<instance>` "
        "(cap=5000 events, TTL=1h)."
    )

    settings = load_settings()
    instances = _instance_ids(settings)
    if not instances:
        st.warning("No instances configured.")
        return
    client = require_redis_connection()

    qp = st.query_params
    qp_task = (qp.get("task_id") or "").strip() if isinstance(qp, dict) else ""
    qp_inst = (qp.get("instance_id") or "").strip() if isinstance(qp, dict) else ""

    c1, c2, c3, c4 = st.columns([2, 3, 2, 1])
    with c1:
        try:
            default_idx = instances.index(qp_inst) if qp_inst in instances else 0
        except ValueError:
            default_idx = 0
        instance_id = st.selectbox(
            "Instance", instances, index=default_idx, key="timeline_instance"
        )
    with c2:
        task_filter = st.text_input(
            "task_id filter (empty = all)",
            value=qp_task,
            key="timeline_task_filter",
            placeholder="ovl:bs1:read_mail_gifts:abc12345",
        )
    with c3:
        event_filter = st.multiselect(
            "Events",
            sorted(EVENT_TYPES),
            default=[],
            key="timeline_event_filter",
            help="Empty = all events.",
        )
    with c4:
        limit = st.number_input(
            "Limit",
            min_value=10,
            max_value=5000,
            value=_DEFAULT_LIMIT,
            step=50,
            key="timeline_limit",
        )

    rows = read_timeline(
        client,
        str(instance_id),
        limit=int(limit),
        task_id=task_filter or None,
        events=event_filter or None,
    )

    if not rows:
        st.info(
            "No events. Either the timeline is empty for this instance or "
            "the filter excluded everything."
        )
        return

    table_rows: list[dict[str, Any]] = []
    for r in rows:
        table_rows.append(
            {
                "time": _fmt_ts(float(r.get("ts") or 0.0)),
                "event": str(r.get("event") or ""),
                "task_id": str(r.get("task_id") or ""),
                "player_id": str(r.get("player_id") or ""),
                "summary": _row_summary(r),
            }
        )
    df = pd.DataFrame(table_rows)
    st.dataframe(
        df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "time": st.column_config.TextColumn("Time", width="small"),
            "event": st.column_config.TextColumn("Event", width="medium"),
            "task_id": st.column_config.TextColumn("Task ID", width="medium"),
            "player_id": st.column_config.TextColumn("Player", width="small"),
            "summary": st.column_config.TextColumn("Summary", width="large"),
        },
    )

    st.caption(
        f"Showing {len(rows)} events (newest first). "
        f"Source key: `wos:debug:timeline:{instance_id}`."
    )

    with st.expander("Raw event payloads", expanded=False):
        for r in rows[:50]:
            stamp = _fmt_ts(float(r.get("ts") or 0.0))
            st.code(
                f"[{stamp}] {r.get('event', '?')}\n"
                + "\n".join(
                    f"  {k} = {v!r}"
                    for k, v in r.items()
                    if k not in ("ts", "event")
                ),
                language="text",
            )

    if not st.session_state.get("timeline_no_autorefresh"):
        # Mild auto-refresh — the stream gets new entries faster than a manual
        # rerun is comfortable, but slower than tab-flicker if it's per-second.
        time.sleep(2)
        st.rerun()


main()
