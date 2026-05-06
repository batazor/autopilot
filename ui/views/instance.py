"""Single instance: reference preview (second column), screenshot, manual controls."""

from __future__ import annotations

import base64
import time
from datetime import timedelta

import streamlit as st

from config.loader import load_settings
from ui.bot_services import ensure_embedded_bot
from ui.preview_display import png_bytes_fitted
from ui.redis_client import (
    fetch_fsm_history,
    get_instance_state,
    get_redis,
    push_instance_command,
    require_redis_connection,
)
from ui.reference_preview import load_rolling_instance_preview
from ui.settings_state import ensure_ui_settings_session_defaults

ensure_embedded_bot()

_PREVIEW_REFRESH_SEC = max(
    0.5,
    float(load_settings().worker.device_reference_snapshot_interval_seconds),
)
_STALE_PREVIEW_AFTER_SEC = max(12.0, _PREVIEW_REFRESH_SEC * 3)


@st.fragment(run_every=timedelta(seconds=_PREVIEW_REFRESH_SEC))
def _reference_preview_fragment(instance_id: str) -> None:
    """Rolling PNG from disk (worker ADB ``device_reference_snapshot_*``)."""
    st.markdown("**Preview** (references/)")
    st.caption(
        f"Reads disk every {_PREVIEW_REFRESH_SEC:.1f} s — file is written by the **bot worker** "
        f"(ADB screencap → `references/temporal/{instance_id}_current_state.png`). "
        "Use **`uv run wos`** / **`ui/app.py`** so the worker runs."
    )

    img_bytes, ref_cap, shot_mtime = load_rolling_instance_preview(instance_id)

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

col_left, col_right = st.columns([3, 2], gap="medium")

with col_left:
    with st.expander("Manual controls", expanded=False):
        if not inst_cfg.player_ids:
            st.warning(
                "No **player_ids** for this instance in config — "
                "add at least one for switch/task controls."
            )
        else:
            mc1, mc2 = st.columns(2)
            with mc1:
                player_pick = st.selectbox("Switch account", inst_cfg.player_ids)
                if st.button("Queue switch"):
                    push_instance_command(
                        client,
                        instance_id,
                        {"cmd": "switch_player", "player_id": player_pick},
                    )
                    st.success("switch_player queued")

            with mc2:
                task_types = sorted(settings.tasks.keys())
                task_pick = st.selectbox("Task type", task_types)
                task_player = st.selectbox(
                    "Player for task", inst_cfg.player_ids, key=f"tp-{instance_id}"
                )
                if st.button("Queue task"):
                    push_instance_command(
                        client,
                        instance_id,
                        {"cmd": "run_task", "task_type": task_pick, "player_id": task_player},
                    )
                    st.success("run_task queued")

        if st.button("Force recovery"):
            push_instance_command(client, instance_id, {"cmd": "recovery"})
            st.success("recovery queued")

    with st.expander("FSM history (per player)", expanded=False):
        if not inst_cfg.player_ids:
            st.caption("No players configured — nothing to show.")
        for pid in inst_cfg.player_ids:
            with st.expander(f"Player {pid}"):
                hist = fetch_fsm_history(client, pid)
                if not hist:
                    st.write("No transitions recorded yet.")
                else:
                    for entry in hist:
                        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(entry["ts"]))
                        st.text(f"{ts}  →  {entry['state']}")

    st.subheader("Stats")
    st.caption("Use logs and Redis state above for operational visibility.")

with col_right:
    _reference_preview_fragment(instance_id)
