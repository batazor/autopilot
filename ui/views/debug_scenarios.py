"""Debug runner for forcing one DSL scenario to the front of an instance queue."""

from __future__ import annotations

import json
import re
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import pandas as pd
import redis
import streamlit as st
import yaml

from actions.tap import click_approval_enabled
from analysis.overlay_manifest import default_analyze_yaml_path
from config.devices import get_device_registry, player_ids_for_device_candidates
from config.loader import InstanceConfig, load_settings
from ui.notifications import push_ui_notification_sync
from ui.redis_client import (
    QueueHistoryRow,
    bump_dsl_preempt_generation,
    fetch_queue_history_rows,
    fetch_queue_rows,
    fetch_running_queue_row,
    get_instance_state,
    push_instance_command,
    require_redis_connection,
)
from ui.views._debug_scenarios_progress import render_step_progress
from ui.views.click_approvals.chrome import render_ui_notifications
from ui.views.click_approvals.ctx import ClickApprovalsCtx
from ui.views.click_approvals.pending import (
    fragment_pending_approval_columns,
    fragment_sync_pending_presence,
)
from ui.views.click_approvals.preview import render_preview_with_point

DEBUG_PRIORITY_DEFAULT = 1_000_000
PREVIEW_MAX_SIDE = 360
HISTORY_FETCH_LIMIT = 50


def _last_scenario_redis_key(instance_id: str) -> str:
    iid = (instance_id or "").strip() or "unknown"
    return f"wos:ui:debug_scenarios:{iid}:last_scenario"


def _load_persisted_last_scenario(client: redis.Redis, instance_id: str) -> str:
    """Return the most recently picked scenario key for this instance (or '')."""
    try:
        raw = client.get(_last_scenario_redis_key(instance_id))
    except redis.RedisError:
        return ""
    if raw is None:
        return ""
    return (raw.decode() if isinstance(raw, bytes) else str(raw)).strip()


def _save_persisted_last_scenario(client: redis.Redis, instance_id: str, key: str) -> None:
    """Persist the most recently picked scenario key so it survives navigation/refresh."""
    if not key:
        return
    with suppress(redis.RedisError):
        # 30 days — long enough to be useful across sessions, short enough to self-clean.
        client.set(_last_scenario_redis_key(instance_id), key, ex=60 * 60 * 24 * 30)


@dataclass(frozen=True)
class ScenarioFile:
    path: Path
    rel_scenarios: str
    repo_rel: str
    key: str
    name: str
    enabled: bool | None
    device_level: bool
    steps: int


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", (s or "").strip()).strip("._-") or "scenario"


def _page_url(page_path: str, params: dict[str, str]) -> str:
    raw = getattr(st.context, "url", None)
    if not (raw and str(raw).strip()):
        raw = "http://localhost:8501/"
    u = urlparse(str(raw))
    path = "/" + page_path.strip("/")
    return urlunparse((u.scheme, u.netloc, path, "", urlencode(params), ""))


def _scenario_param_path(repo_root: Path, raw: object | None) -> Path | None:
    if raw is None:
        return None
    s = raw[0] if isinstance(raw, list) and raw else raw
    s = str(s).strip().replace("\\", "/")
    if not s or "/" in s or s.endswith(".yaml"):
        return None
    scenarios_root = repo_root / "scenarios"
    hits = [
        p for p in scenarios_root.rglob(f"{s}.yaml")
        if not p.relative_to(scenarios_root).as_posix().startswith("drafts/")
    ]
    if not hits:
        return None
    hits.sort(key=lambda p: (len(p.relative_to(scenarios_root).parts), p.as_posix()))
    return hits[0]


def _scenario_param_value(raw: object | None) -> str:
    if raw is None:
        return ""
    s = raw[0] if isinstance(raw, list) and raw else raw
    s = str(s or "").strip().replace("\\", "/")
    return s


def _list_scenario_files(repo_root: Path) -> list[ScenarioFile]:
    scenarios_root = repo_root / "scenarios"
    if not scenarios_root.is_dir():
        return []
    out: list[ScenarioFile] = []
    for p in sorted(scenarios_root.rglob("*.yaml")):
        rel = p.relative_to(scenarios_root).as_posix()
        if rel.startswith("drafts/"):
            continue
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        steps = raw.get("steps")
        enabled_raw = raw.get("enabled")
        out.append(
            ScenarioFile(
                path=p,
                rel_scenarios=rel,
                repo_rel=p.relative_to(repo_root).as_posix(),
                key=p.stem,
                name=str(raw.get("name") or p.stem),
                enabled=enabled_raw if isinstance(enabled_raw, bool) else None,
                device_level=raw.get("device_level") is True,
                steps=len(steps) if isinstance(steps, list) else 0,
            )
        )
    return out


def _players_for_instance(inst: InstanceConfig, active_player: str) -> list[str]:
    players = player_ids_for_device_candidates(inst.bluestacks_window_title, inst.instance_id)
    if not players:
        players = get_device_registry().all_player_ids()
    out: list[str] = []
    for pid in players:
        if pid and pid not in out:
            out.append(pid)
    if active_player and active_player not in out:
        out.append(active_player)
    return out


def _player_options_for_instance(
    inst: InstanceConfig,
    *,
    active_player: str,
    device_level: bool,
) -> list[tuple[str, str]]:
    opts: list[tuple[str, str]] = []
    for pid in _players_for_instance(inst, active_player):
        label = pid
        if active_player and pid == active_player:
            label = f"{pid} (active)"
        opts.append((label, pid))
    if device_level:
        opts.append(("(device-level / no player)", ""))
    return opts


def _enqueue_debug_scenario(
    client: redis.Redis,
    *,
    instance_id: str,
    player_id: str,
    scenario_key: str,
    priority: int,
    start_step_index: int,
) -> str:
    now = time.time()
    task_id = f"ui:debug:{instance_id}:{_slug(scenario_key)}:{int(now)}"
    payload: dict[str, object] = {
        "task_id": task_id,
        "player_id": player_id,
        "task_type": scenario_key,
        "priority": int(priority),
        "run_at": float(now),
        "instance_id": instance_id,
        "debug": True,
        "source": "ui.debug_scenarios",
    }
    if start_step_index > 0:
        payload["start_step_index"] = int(start_step_index)
    # Ask any in-flight DSL scenario on this instance to exit so this task runs next.
    with suppress(Exception):
        bump_dsl_preempt_generation(client, instance_id)
    client.zadd(
        f"wos:queue:{instance_id}",
        {json.dumps(payload, ensure_ascii=False): float(now)},
    )
    # Match `who_i_am` / OCR: scheduler and `_resolve_queue_item_player` gate on
    # `wos:instance:*:state.active_player`. Debug enqueue already carries `player_id`
    # in the payload — persist it so player-bound scenarios are not stuck waiting.
    pid = str(player_id or "").strip()
    if pid:
        client.hset(
            f"wos:instance:{instance_id}:state",
            mapping={
                "active_player": pid,
                "active_player_at": str(now),
            },
        )
    # Worker idle loop uses BRPOP on this queue so it picks up new work without a 2s poll delay.
    push_instance_command(client, instance_id, {"cmd": "wake"})
    return task_id


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


def _active_player_in_game_id(*, client: Any, inst: str) -> str:
    row = get_instance_state(client, inst) or {}
    active = str(row.get("active_player") or "").strip()
    if not active:
        return "-"
    try:
        raw = client.hget(f"wos:player:{active}:state", "player_id")
    except Exception:
        return "-"
    if raw is None:
        return "-"
    val = raw.decode() if isinstance(raw, bytes) else str(raw)
    return val.strip() or "-"


@st.fragment(run_every=timedelta(seconds=1))
def _render_debug_header(inst: str) -> None:
    row = get_instance_state(client, inst) or {}
    node = str(row.get("current_screen") or "").strip() or "-"
    current = str(row.get("current_scenario") or "").strip() or "-"
    pid_in_game = _active_player_in_game_id(client=client, inst=inst)

    st.title(f"Debug · Scenario runner · {inst}")
    st.caption(f"node: `{node}` · player_id: `{pid_in_game}` · scenario: `{current}`")


@st.fragment(run_every=timedelta(seconds=1))
def _render_live_screenshot(ctx: ClickApprovalsCtx, inst: str) -> None:
    st.subheader("Screenshot")
    render_preview_with_point(
        ctx=ctx,
        instance_id=inst,
        x=None,
        y=None,
        payload=None,
        where=st,
    )


@st.fragment(run_every=timedelta(seconds=1))
def _render_run_status(
    inst: str,
    player_id: str,
    scenario_key: str,
    scenario_total_steps: int,
) -> None:
    st.subheader("Run status")
    now = time.time()
    state = get_instance_state(client, inst)
    running = fetch_running_queue_row(client, instance_id=inst)
    last_task_id = str(st.session_state.get("debug_scenario_last_task_id") or "").strip()

    if running is not None and running.task_id:
        kind = "debug run" if running.task_id == last_task_id else "worker task"
        st.info(
            f"Running {kind}: `{running.task_type}` · player "
            f"`{running.player_id or 'device'}` · started {_rel_time(running.started_at, now)}"
        )
    else:
        st.success("No task is running on this instance.")

    pending = [
        r for r in fetch_queue_rows(client)
        if r.instance_id == inst and r.task_type == scenario_key and r.player_id == player_id
    ]
    if pending:
        top = sorted(pending, key=lambda r: (-r.priority, r.scheduled_at))[:5]
        st.markdown("**Pending selected scenario**")
        st.dataframe(
            [
                {
                    "scheduled": _rel_time(r.scheduled_at, now),
                    "priority": r.priority,
                    "task_id": r.task_id,
                }
                for r in top
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Selected scenario is not pending for this player.")

    current_task = str(state.get("current_task_player") or "").strip()
    blocked = str(state.get("queue_blocked_reason") or "").strip()
    if current_task or blocked:
        with st.expander("Worker context", expanded=bool(blocked)):
            st.json(
                {
                    "active_player": str(state.get("active_player") or ""),
                    "current_screen": str(state.get("current_screen") or ""),
                    "current_scenario": str(state.get("current_scenario") or ""),
                    "current_task_player": current_task,
                    "current_task_region": str(state.get("current_task_region") or ""),
                    "queue_blocked_reason": blocked,
                }
            )


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


def _replay_history_row(
    inst_id: str,
    scenario_key: str,
    scenario_total_steps: int,
    h: QueueHistoryRow,
) -> str:
    """Re-enqueue a historical run with its original player/priority/start_step."""
    payload = h.payload or {}
    pid = str(payload.get("player_id") or h.player_id or "").strip()
    try:
        prio = int(payload.get("priority") or 0)
    except (TypeError, ValueError):
        prio = DEBUG_PRIORITY_DEFAULT
    if prio <= 0:
        prio = DEBUG_PRIORITY_DEFAULT
    try:
        start_step = int(payload.get("start_step_index") or 0)
    except (TypeError, ValueError):
        start_step = 0
    # Scenario may have grown/shrunk since the historical run.
    if scenario_total_steps > 0:
        start_step = max(0, min(start_step, scenario_total_steps - 1))
    task_id = _enqueue_debug_scenario(
        client,
        instance_id=inst_id,
        player_id=pid,
        scenario_key=scenario_key,
        priority=prio,
        start_step_index=start_step,
    )
    st.session_state["debug_scenario_last_task_id"] = task_id
    parts = [f"Replayed `{scenario_key}`", f"task `{task_id}`", f"priority `{prio}`"]
    if start_step > 0:
        parts.append(f"step `{start_step}`")
    if pid:
        parts.append(f"player `{pid}`")
    st.toast(" · ".join(parts))
    return task_id


@st.fragment(run_every=timedelta(seconds=3))
def _render_scenario_history(
    inst_id: str,
    scenario_key: str,
    scenario_total_steps: int,
) -> None:
    st.subheader(f"Last run history · `{scenario_key}`")
    start_step_state_key = f"debug_scenarios_start_step::{scenario_key}"
    history = fetch_queue_history_rows(
        client, instance_id=inst_id, limit=HISTORY_FETCH_LIMIT
    )
    matching = [
        h for h in history
        if (h.scenario or h.task_type) == scenario_key
    ]
    if not matching:
        st.caption(
            f"No completed runs for `{scenario_key}` on `{inst_id}` "
            f"in the last {HISTORY_FETCH_LIMIT} entries."
        )
        return

    last = matching[0]
    last_task_id = str(st.session_state.get("debug_scenario_last_task_id") or "").strip()
    is_my_task = bool(last_task_id) and last.task_id == last_task_id

    started_str = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last.started_at))
        if last.started_at else "—"
    )
    finished_str = (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last.finished_at))
        if last.finished_at else "—"
    )
    status_label = "✅ success" if last.success else "❌ fail"
    suffix = " · (debug run from this session)" if is_my_task else ""

    hdr_left, hdr_right = st.columns([5, 1], vertical_alignment="center")
    with hdr_left:
        st.markdown(
            f"**{status_label}**{suffix} · player `{last.player_id or 'device'}` · "
            f"steps {_history_steps_summary(last)} · duration `{last.duration_s:.1f}s`"
        )
        st.caption(
            f"started `{started_str}` · finished `{finished_str}` · "
            f"task_id `{last.task_id}`"
        )
        if last.reason:
            st.caption(f"reason: `{last.reason}`")
        if last.error:
            st.error(last.error)
    with hdr_right:
        if st.button(
            "▶ Replay",
            key=f"history_replay_last::{last.task_id}",
            help="Re-enqueue with player / priority / start_step from this run's payload.",
            width="stretch",
        ):
            _replay_history_row(inst_id, scenario_key, scenario_total_steps, last)
            st.rerun(scope="app")

    if last.steps_trace:
        with st.expander("DSL step trace", expanded=False):
            trace_df = pd.DataFrame(last.steps_trace)
            if "i" in trace_df.columns:
                # `i` mixes ints and dotted strings (nested iters); pyarrow needs one dtype.
                trace_df["i"] = trace_df["i"].astype(str)
            event = st.dataframe(
                trace_df,
                hide_index=True,
                width="stretch",
                selection_mode="single-row",
                on_select="rerun",
                key=f"history_trace_df::{last.task_id}",
            )
            sel_rows = []
            with suppress(AttributeError, KeyError):
                sel_rows = list(event.selection.get("rows", []))  # type: ignore[union-attr]
            if sel_rows:
                row_idx = int(sel_rows[0])
                sel_row = last.steps_trace[row_idx] if 0 <= row_idx < len(last.steps_trace) else {}
                raw_i = str(sel_row.get("i") or "")
                summary = str(sel_row.get("summary") or "")
                # Only top-level integer indices map to a `start_step_index`; nested
                # rows like "6.0" come from loop iterations and aren't resumable.
                if raw_i.isdigit():
                    step_n = int(raw_i)
                    if scenario_total_steps and step_n > scenario_total_steps - 1:
                        st.warning(
                            f"Step #{step_n} is out of range for current scenario "
                            f"({scenario_total_steps} steps). The scenario may have changed."
                        )
                    else:
                        if st.button(
                            f"⤴ Use step #{step_n} as start_step_index"
                            + (f"  ·  _{summary}_" if summary else ""),
                            key=f"history_trace_use::{last.task_id}::{row_idx}",
                            width="stretch",
                        ):
                            st.session_state[start_step_state_key] = step_n
                            st.toast(
                                f"Start step set to {step_n}. "
                                "Press 'Run scenario now' above."
                            )
                            st.rerun(scope="app")
                else:
                    st.caption(
                        f"Step index `{raw_i}` is a nested loop iteration; "
                        "jump-to-step supports only top-level steps."
                    )

    with st.expander("Last run JSON", expanded=True):
        st.code(
            json.dumps(last.payload or {}, ensure_ascii=False, indent=2),
            language="json",
        )

    if len(matching) > 1:
        with st.expander(f"Previous runs ({len(matching) - 1})", expanded=False):
            for h in matching[1:11]:
                hstarted = (
                    time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(h.started_at))
                    if h.started_at else "—"
                )
                icon = "✅" if h.success else "❌"
                tail = h.reason or h.error or h.task_id
                if len(tail) > 80:
                    tail = f"{tail[:77]}..."
                row_left, row_right = st.columns([5, 1], vertical_alignment="center")
                with row_left:
                    st.markdown(
                        f"{icon} `{hstarted}` · {h.duration_s:.1f}s · "
                        f"player `{h.player_id or 'device'}` · "
                        f"steps {_history_steps_summary(h)} · {tail}"
                    )
                with row_right:
                    if st.button(
                        "▶ Replay",
                        key=f"history_replay::{h.task_id}",
                        help="Re-enqueue with this run's player/priority/start_step.",
                        width="stretch",
                    ):
                        _replay_history_row(inst_id, scenario_key, scenario_total_steps, h)
                        st.rerun(scope="app")
                with st.expander("JSON", expanded=False):
                    st.code(
                        json.dumps(h.payload or {}, ensure_ascii=False, indent=2),
                        language="json",
                    )


@st.fragment(run_every=timedelta(seconds=1))
def _render_approval_heartbeat(ctx: ClickApprovalsCtx) -> None:
    enabled = click_approval_enabled(ctx.instance_id)
    if enabled:
        client.set(ctx.enabled_key, "1")
        client.set(ctx.hb_key, str(time.time()), ex=5)
    else:
        client.set(ctx.enabled_key, "0")
        client.delete(ctx.hb_key)
    has_current = bool(client.get(ctx.current_key))
    st.caption(
        f"Approval mode: **{'ON' if enabled else 'OFF'}** · "
        f"Heartbeat: **{'ON' if enabled else 'OFF'}** · "
        f"Pending request: **{'YES' if has_current else 'NO'}**."
    )

repo_root = _repo_root()
settings = load_settings()
client = require_redis_connection()

files = _list_scenario_files(repo_root)
if not files:
    st.warning("No runnable scenario YAML found under `scenarios/`.")
    st.stop()

st.caption("Force one DSL scenario to run next on an instance, ahead of normal queued work.")

labels = [
    f"{sf.rel_scenarios} · {sf.name} · key={sf.key}"
    for sf in files
]

inst_ids = [inst.instance_id for inst in settings.instances]
if not inst_ids:
    st.error("No instances configured.")
    st.stop()

inst_idx = 0
inst = settings.instances[inst_idx]
selected_inst_id = st.selectbox("Instance", inst_ids, index=inst_idx)
inst = next(i for i in settings.instances if i.instance_id == selected_inst_id)

# Default scenario picker: URL `?scenario=…` wins; otherwise fall back to the
# per-instance last-picked value in Redis so reload / sidebar nav doesn't reset
# the selection.
target = _scenario_param_path(repo_root, st.query_params.get("scenario"))
if target is None:
    persisted_key = _load_persisted_last_scenario(client, inst.instance_id)
    if persisted_key:
        target = _scenario_param_path(repo_root, persisted_key)
default_index = 0
if target is not None:
    for i, sf in enumerate(files):
        if sf.path.resolve() == target.resolve():
            default_index = i
            break

ctx = ClickApprovalsCtx(
    instance_id=inst.instance_id,
    repo_root=repo_root,
    area_path=repo_root / "area.json",
    analyze_path=default_analyze_yaml_path(repo_root),
    preview_max_side=PREVIEW_MAX_SIDE,
    probe_overlay_max_side=900,
    region_crop_max_side=220,
)

_render_debug_header(inst.instance_id)
render_ui_notifications(inst.instance_id, client=client)

enabled_now = click_approval_enabled(inst.instance_id)
enabled_ui = st.toggle(
    "Approval mode (ON = require approve for ADB input and DSL set_node)",
    value=enabled_now,
    key=f"debug_scenarios_approval_enabled::{inst.instance_id}",
)
if enabled_ui != enabled_now:
    client.set(ctx.enabled_key, "1" if enabled_ui else "0")
    if not enabled_ui:
        client.delete(ctx.hb_key)
    st.rerun()
_render_approval_heartbeat(ctx)

st.divider()
st.subheader("Run scenario")

run_left, run_right = st.columns([3.0, 1.0], vertical_alignment="bottom")
with run_left:
    scenario_pick_key = "debug_scenario_pick"
    query_sync_key = "debug_scenario_last_query"
    current_scenario_param = _scenario_param_value(st.query_params.get("scenario"))
    last_synced_param = str(st.session_state.get(query_sync_key) or "")
    if target is not None and current_scenario_param != last_synced_param:
        current_pick = st.session_state.get(scenario_pick_key)
        current_sf = (
            files[int(current_pick)]
            if isinstance(current_pick, int) and 0 <= current_pick < len(files)
            else None
        )
        if current_sf is None or current_sf.path.resolve() != target.resolve():
            st.session_state[scenario_pick_key] = default_index
        st.session_state[query_sync_key] = current_scenario_param

    if scenario_pick_key not in st.session_state:
        st.session_state[scenario_pick_key] = default_index

    # Substring filter sits in front of the selectbox because Streamlit's built-in
    # dropdown matcher is fuzzy ("claim" matches labels that don't actually contain
    # the substring), which is misleading for scenario lookup.
    filter_q = st.text_input(
        "Filter",
        key="debug_scenario_filter",
        placeholder="Substring of path / name / key (case-insensitive)",
    )
    q = filter_q.strip().lower()
    if q:
        visible_indices = [i for i, lbl in enumerate(labels) if q in lbl.lower()]
    else:
        visible_indices = list(range(len(files)))

    if not visible_indices:
        st.warning(f"No scenarios match `{filter_q}`.")
        st.stop()

    current_pick_val = st.session_state.get(scenario_pick_key)
    if not (isinstance(current_pick_val, int) and current_pick_val in visible_indices):
        st.session_state[scenario_pick_key] = visible_indices[0]

    picked_idx = st.selectbox(
        "Scenario",
        visible_indices,
        format_func=lambda i: labels[int(i)],
        key=scenario_pick_key,
    )
    scenario = files[int(picked_idx)]

current_scenario_param = _scenario_param_value(st.query_params.get("scenario"))
if current_scenario_param != scenario.key:
    st.query_params["scenario"] = scenario.key
    st.session_state["debug_scenario_last_query"] = scenario.key

# Persist last-picked scenario (per instance) so refresh / sidebar nav restores it.
persist_state_key = f"debug_scenario_persisted::{inst.instance_id}"
if st.session_state.get(persist_state_key) != scenario.key:
    _save_persisted_last_scenario(client, inst.instance_id, scenario.key)
    st.session_state[persist_state_key] = scenario.key

with run_right:
    st.link_button(
        "Open in Scenarios",
        _page_url("scenarios", {"q": scenario.key}),
        width="stretch",
    )

same_key = [sf for sf in files if sf.key == scenario.key]
if len(same_key) > 1:
    st.warning(
        "This key is duplicated. The worker resolves by filename stem and will "
        "choose the shortest path first: "
        + ", ".join(sf.rel_scenarios for sf in same_key)
    )

meta = st.columns([2.0, 1.0, 1.0, 1.0, 1.6])
meta[0].markdown(f"**File**: `{scenario.repo_rel}`")
meta[1].markdown(f"**Key**: `{scenario.key}`")
meta[2].markdown(f"**Enabled**: `{scenario.enabled}`")
meta[3].markdown(f"**Device**: `{scenario.device_level}`")
meta[4].markdown(f"**Steps**: `{scenario.steps}`")

state = get_instance_state(client, inst.instance_id)
active_player = str(state.get("active_player") or "").strip()
player_options = _player_options_for_instance(
    inst,
    active_player=active_player,
    device_level=scenario.device_level,
)

if not player_options:
    st.error(
        "No known player id for this instance. Run `who_i_am` first "
        "or add players in device config."
    )
    st.stop()

# Honor `?player=` so cross-page links (e.g. Queue → Debug) can pre-select the
# player; fall back to the in-session pick when no URL hint is given.
player_param_raw = st.query_params.get("player")
player_param = ""
if player_param_raw is not None:
    raw = player_param_raw[0] if isinstance(player_param_raw, list) and player_param_raw else player_param_raw
    player_param = str(raw or "").strip()
player_pick_key = f"debug_scenario_player_pick::{inst.instance_id}"
player_idx_default = 0
if player_param:
    for i, (_, pid_opt) in enumerate(player_options):
        if pid_opt.strip() == player_param:
            player_idx_default = i
            break
if player_pick_key not in st.session_state:
    st.session_state[player_pick_key] = player_idx_default
# If the URL says to pre-select a specific player, override the cached pick.
elif player_param and st.session_state.get(player_pick_key) != player_idx_default:
    st.session_state[player_pick_key] = player_idx_default

cached_player_idx = st.session_state.get(player_pick_key)
if not (isinstance(cached_player_idx, int) and 0 <= cached_player_idx < len(player_options)):
    st.session_state[player_pick_key] = 0
pid_choice = st.selectbox(
    "Player",
    range(len(player_options)),
    format_func=lambda i: player_options[int(i)][0],
    key=player_pick_key,
    help="Known players come from the device config plus the current active player.",
)
pid = player_options[int(pid_choice)][1].strip()

if not pid and not scenario.device_level:
    st.error("Player-bound scenario needs an explicit `player_id`.")
    st.stop()

if not active_player and pid and not scenario.device_level:
    st.warning(
        "`active_player` is empty. The worker will keep player-bound scenarios "
        "waiting until `who_i_am` or another identity probe writes the active player."
    )

# Use stable session keys so other fragments (history "Replay", trace
# "Start from here") can pre-fill these inputs before re-render.
priority_key = "debug_scenarios_priority"
start_step_key = f"debug_scenarios_start_step::{scenario.key}"
if priority_key not in st.session_state:
    st.session_state[priority_key] = DEBUG_PRIORITY_DEFAULT
max_start = max(0, scenario.steps - 1)
cached_start = st.session_state.get(start_step_key)
if not (isinstance(cached_start, int) and 0 <= cached_start <= max_start):
    st.session_state[start_step_key] = 0

priority = st.number_input(
    "Priority",
    min_value=1,
    max_value=10_000_000,
    step=10_000,
    key=priority_key,
    help="Higher than normal overlay/routine priorities, so this runs first among pending tasks.",
)
start_step_index = st.number_input(
    "Start step index",
    min_value=0,
    max_value=max_start,
    step=1,
    key=start_step_key,
)

if st.button(
    "Run scenario now",
    type="primary",
    width="stretch",
    help="Preempts the in-flight DSL scenario (cooperative — exits at next step) and enqueues this one at the front.",
):
    if scenario.steps <= 0:
        st.warning(
            f"`{scenario.key}` has no steps yet. Add at least one step in Scenarios editor, "
            "then run it again."
        )
    else:
        # Snapshot the in-flight scenario *before* enqueue so the success message
        # can tell the user what got preempted.
        preempted_running = fetch_running_queue_row(client, instance_id=inst.instance_id)
        preempted_label = ""
        if preempted_running is not None and preempted_running.task_id:
            preempted_label = (
                f"{preempted_running.task_type} (task `{preempted_running.task_id}`)"
            )
        task_id = _enqueue_debug_scenario(
            client,
            instance_id=inst.instance_id,
            player_id=pid,
            scenario_key=scenario.key,
            priority=int(priority),
            start_step_index=int(start_step_index),
        )
        st.session_state["debug_scenario_last_task_id"] = task_id
        push_ui_notification_sync(
            client,
            inst.instance_id,
            kind="debug_scenarios.enqueue",
            message=(
                f"Scenario enqueued: {scenario.key} "
                f"(priority {int(priority)}"
                + (f", step {int(start_step_index)}" if int(start_step_index) > 0 else "")
                + (f", player {pid}" if pid else "")
                + ")"
            ),
            level="info",
            payload={
                "task_id": task_id,
                "scenario": scenario.key,
                "priority": int(priority),
                "start_step_index": int(start_step_index),
                "player_id": pid or "",
                "instance_id": inst.instance_id,
            },
        )
        msg = f"Enqueued `{task_id}` with priority `{int(priority)}`."
        if preempted_label:
            msg = f"Preempted {preempted_label}. " + msg
        st.success(msg)
        st.rerun()

st.divider()
fragment_sync_pending_presence(inst=inst.instance_id, client=client)
# Render the progress bar above the approval / screenshot area so it stays
# in place across both states — operator never loses sight of step X/N,
# including while a click-approval card is up.
render_step_progress(
    client=client,
    instance_id=inst.instance_id,
    scenario_key=scenario.key,
    scenario_total_steps=int(scenario.steps),
)
if client.get(ctx.current_key):
    fragment_pending_approval_columns(
        ctx=ctx,
        client=client,
        inst=inst.instance_id,
        curr_key=ctx.current_key,
    )
else:
    col_img, col_status = st.columns([1, 1.25], gap="large")
    with col_img:
        _render_live_screenshot(ctx, inst.instance_id)
    with col_status:
        st.subheader("Approvals")
        st.success("No pending click requests.")
        _render_run_status(inst.instance_id, pid, scenario.key, int(scenario.steps))

st.divider()
_render_scenario_history(inst.instance_id, scenario.key, int(scenario.steps))

with st.expander("Raw YAML", expanded=False):
    st.code(scenario.path.read_text(encoding="utf-8"), language="yaml")
