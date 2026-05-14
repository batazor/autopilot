"""Single instance: reference preview (second column), screenshot, manual controls."""

from __future__ import annotations

import base64
import time
from datetime import timedelta
from pathlib import Path

import streamlit as st

from config.devices import player_ids_for_device
from config.loader import load_settings
from ui.bot_services import ensure_embedded_bot, restart_embedded_bot
from ui.preview_display import png_bytes_fitted
from ui.redis_client import (
    count_queue_tasks_for_instance,
    fetch_next_queue_row_for_instance,
    fetch_queue_history_rows,
    get_instance_state,
    get_redis,
    push_instance_command,
    require_redis_connection,
)
from ui.reference_preview import load_rolling_instance_preview, rolling_live_preview_path
from ui.scenario_keys import runnable_scenario_keys
from ui.settings_state import ensure_ui_settings_session_defaults
from ui.views._debug_scenarios_progress import render_active_scenario_progress

_REPO = Path(__file__).resolve().parents[2]

ensure_embedded_bot()

_PREVIEW_REFRESH_SEC = max(
    0.5,
    float(load_settings().worker.device_reference_snapshot_interval_seconds),
)
# Account for the busy-cadence too: during a long task the rolling preview
# only updates every ``device_reference_snapshot_busy_interval_seconds``, so
# the stale threshold has to cover *that* cadence to avoid spurious
# "preview stale" warnings while the bot is just slowly snapshotting.
_PREVIEW_REFRESH_BUSY_SEC = max(
    _PREVIEW_REFRESH_SEC,
    float(load_settings().worker.device_reference_snapshot_busy_interval_seconds),
)
_STALE_PREVIEW_AFTER_SEC = max(12.0, _PREVIEW_REFRESH_BUSY_SEC * 3)
_PREVIEW_CACHE_KEY = "_instance_preview_cache"


def _load_preview_cached(instance_id: str) -> tuple[bytes | None, str, float | None]:
    """Return PNG bytes from session_state cache; read from disk only when mtime changes."""
    path = rolling_live_preview_path(instance_id)
    cache: dict = st.session_state.setdefault(_PREVIEW_CACHE_KEY, {})
    if not path.is_file():
        cache.pop(instance_id, None)
        return None, "", None
    mtime = path.stat().st_mtime
    entry = cache.get(instance_id)
    if entry is not None and entry["mtime"] == mtime:
        return entry["bytes"], entry["rel"], mtime
    img_bytes, rel, _ = load_rolling_instance_preview(instance_id)
    if img_bytes is not None:
        cache[instance_id] = {"mtime": mtime, "bytes": img_bytes, "rel": rel}
    return img_bytes, rel, mtime


@st.fragment(run_every=timedelta(seconds=_PREVIEW_REFRESH_SEC))
def _reference_preview_fragment(instance_id: str) -> None:
    """Rolling PNG from disk (worker ADB ``device_reference_snapshot_*``)."""
    st.markdown("**Preview** (references/)")
    st.caption(
        f"Reads disk every {_PREVIEW_REFRESH_SEC:.1f} s — file is written by the **bot worker** "
        f"(ADB screencap → `references/temporal/{instance_id}_current_state.png`). "
        "Use **`uv run wos`** / **`ui/app.py`** so the worker runs."
    )

    img_bytes, ref_cap, shot_mtime = _load_preview_cached(instance_id)

    if img_bytes is not None:
        now_ts = time.time()
        age_s: float | None = (now_ts - shot_mtime) if shot_mtime is not None else None

        if age_s is not None and age_s > _STALE_PREVIEW_AFTER_SEC:
            row = get_instance_state(get_redis(), instance_id)
            if not row.get("worker_started_at"):
                st.warning(
                    f"PNG is ~{int(age_s)} s old — **no worker row** in Redis yet "
                    "(bot thread starting, wrong **redis.url**, or broker down). "
                    "Wait a few seconds after UI load; "
                    "otherwise check **`uv run wos`** / terminal logs."
                )
            elif row.get("paused") == "1":
                st.warning(
                    f"PNG is ~{int(age_s)} s old — instance **paused** (Overview ▶ Resume). "
                    "Rolling ADB snapshots are not written while paused."
                )
            elif str(row.get("state", "")).lower() in {"crashed", "restarting"}:
                st.warning(
                    f"PNG stale (~{int(age_s)} s) — worker state **`{row.get('state')}`**. "
                    "Fix emulator/worker, then check logs."
                )
            else:
                st.warning(
                    f"PNG is ~{int(age_s)} s old — worker alive but **ADB screencap** may fail "
                    "(**`adb devices`**, **`bluestacks_window_title`** serial, "
                    "**`worker.adb_executable`**). "
                    "See stderr: **ADB rolling snapshot failed** / **cv2.imwrite**. "
                    "Confirm Streamlit and worker share **the same repo** "
                    "(path **references/temporal/**)."
                )

        _preview_max_side = 400
        fitted, native, _ = png_bytes_fitted(img_bytes, _preview_max_side)
        ts_label = (
            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(shot_mtime))
            if shot_mtime is not None
            else "—"
        )
        cap_txt = f"{ref_cap} · {instance_id}" if ref_cap else instance_id
        # Include file age so the caption changes even if local clock display matches a prior frame.
        age_hint = f" (age {int(age_s)}s)" if age_s is not None and age_s >= 0 else ""
        caption = f"{ts_label}{age_hint} · {cap_txt} · {native[0]}×{native[1]}"
        # HTML + mtime comment busts Streamlit/browser reuse of identical ``st.image`` payloads.
        b64 = base64.standard_b64encode(fitted).decode("ascii")
        bust = f"{shot_mtime:.6f}" if shot_mtime is not None else str(time.time())
        st.markdown(
            f"<!-- rolling-preview:{instance_id}:{bust} -->"
            f'<img src="data:image/png;base64,{b64}" '
            'style="max-width:100%;width:auto;height:auto;max-height:420px;display:block" />',
            unsafe_allow_html=True,
        )
        st.caption(caption)
    else:
        temporal_file = f"`references/temporal/{instance_id}_current_state.png`."
        st.info(
            "No rolling PNG yet — worker must run (**`uv run wos`**) and **ADB** must return a PNG "
            f"(serial **`bluestacks_window_title`**). File: {temporal_file} "
            "Manual **Screenshot** writes the same path via ADB."
        )


st.title("Instance")

# Show persistent navigation errors written by the worker when a route is missing.
_nav_err_client = require_redis_connection()
if _nav_err_client:
    try:
        _inst_ids = [i.instance_id for i in load_settings().instances]
        for _iid in _inst_ids:
            _nav_err = _nav_err_client.hget(f"wos:instance:{_iid}:state", "nav_error")
            if _nav_err:
                _nav_err_s = _nav_err.decode() if isinstance(_nav_err, bytes) else str(_nav_err)
                if _nav_err_s.strip():
                    st.error(f"**Nav error [{_iid}]:** {_nav_err_s} — add missing region/edge to `screen_graph.py`")
    except Exception:
        pass

if st.button("Restart bot", help="Stop and start embedded workers/scheduler"):
    try:
        restart_embedded_bot()
    except RuntimeError as exc:
        st.error(f"Bot restart failed: {exc}")
        st.stop()
    st.success("Bot restart triggered")
    st.rerun()

ensure_ui_settings_session_defaults()

settings = load_settings()
client = require_redis_connection()

params = st.query_params
_default_iid = settings.instances[0].instance_id if settings.instances else ""
instance_id = params.get("instance_id", _default_iid)

choices = [i.instance_id for i in settings.instances]
if not choices:
    st.warning("No instances in config.")
    st.stop()

if instance_id not in choices:
    instance_id = choices[0]

instance_id = st.selectbox("Instance", choices, index=choices.index(instance_id))

inst_cfg = next(i for i in settings.instances if i.instance_id == instance_id)

# Operator glance: live progress + queue size + next due.
render_active_scenario_progress(
    client=client,
    instance_id=instance_id,
    repo_root=_REPO,
)
queue_n = count_queue_tasks_for_instance(client, instance_id=instance_id)
next_row = fetch_next_queue_row_for_instance(client, instance_id=instance_id)
g1, g2 = st.columns(2)
with g1:
    st.metric("Queue size", str(queue_n))
with g2:
    if next_row is not None and next_row.scheduled_at:
        ts = time.strftime("%H:%M:%S", time.localtime(next_row.scheduled_at))
        st.metric("Next due", ts)
        st.caption(f"{next_row.task_type} · `{next_row.task_id}`")
    else:
        st.metric("Next due", "—")

col_left, col_right = st.columns([3, 2], gap="medium")

with col_left:
    st.subheader("Manual controls")
    _inst_player_ids = player_ids_for_device(inst_cfg.bluestacks_window_title)
    if not _inst_player_ids:
        st.warning(
            "No **player_ids** for this instance in `db/devices.yaml` — "
            "run the bot so `fetch_player` populates it."
        )

    mc_tabs = st.tabs(["🔀 Switch account", "▶ Run task", "🔄 Restart game"])

    with mc_tabs[0]:
        if _inst_player_ids:
            player_pick = st.selectbox(
                "Account",
                _inst_player_ids,
                key=f"mc-switch-{instance_id}",
                help="Worker will switch to this account before the next task.",
            )
            if st.button("Queue switch", key=f"mc-switch-btn-{instance_id}", width="stretch"):
                push_instance_command(
                    client,
                    instance_id,
                    {"cmd": "switch_player", "player_id": player_pick},
                )
                st.success(f"switch_player → `{player_pick}` queued")
        else:
            st.caption("No accounts available for this instance.")

    with mc_tabs[1]:
        if _inst_player_ids:
            task_types = list(runnable_scenario_keys(str(_REPO / "scenarios")))
            if not task_types:
                st.caption(
                    "No runnable scenarios found under `scenarios/` "
                    "(excluding drafts/ and `{hero}` templates)."
                )
            else:
                tp1, tp2 = st.columns(2)
                with tp1:
                    task_pick = st.selectbox(
                        "Task type",
                        task_types,
                        key=f"mc-task-type-{instance_id}",
                        help=(
                            "DSL scenario keys (filename without ``.yaml``). "
                            "Hero-templated scenarios are launched per-hero "
                            "from the Debug Runner page instead."
                        ),
                    )
                with tp2:
                    task_player = st.selectbox(
                        "Player",
                        _inst_player_ids,
                        key=f"mc-task-player-{instance_id}",
                    )
                if st.button(
                    "Queue task", key=f"mc-task-btn-{instance_id}", width="stretch"
                ):
                    push_instance_command(
                        client,
                        instance_id,
                        {
                            "cmd": "run_task",
                            "task_type": task_pick,
                            "player_id": task_player,
                        },
                    )
                    st.success(f"run_task `{task_pick}` → `{task_player}` queued")
        else:
            st.caption("No accounts available — can't queue a task.")

    with mc_tabs[2]:
        st.caption("Hard-restart the game on this instance.")
        if st.button(
            "Restart game",
            key=f"mc-restart-{instance_id}",
            type="primary",
            width="stretch",
        ):
            push_instance_command(client, instance_id, {"cmd": "restart"})
            st.success("restart queued")

    st.divider()
    with st.expander("Scenario history", expanded=False):
        st.caption(
            "Last 50 finished scenarios for this instance, grouped by player "
            "(source: `wos:queue:history:<instance_id>`)."
        )
        hist_rows = fetch_queue_history_rows(client, instance_id=instance_id, limit=50)
        if not hist_rows:
            st.write("No completed scenarios recorded yet.")
        else:
            buckets: dict[str, list] = {}
            for h in hist_rows:
                buckets.setdefault(h.player_id or "(device)", []).append(h)
            for pid, rows in buckets.items():
                with st.expander(f"Player `{pid}` · {len(rows)} run(s)"):
                    for h in rows:
                        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(h.started_at))
                        mark = "✅" if h.success else "❌"
                        detail = h.reason or h.error or h.task_id
                        st.text(
                            f"{ts}  {mark}  {h.scenario or h.task_type}"
                            f"  ·  {h.duration_s:.1f}s  ·  {detail}"
                        )

with col_right:
    _reference_preview_fragment(instance_id)
