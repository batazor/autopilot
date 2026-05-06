"""Single instance: reference preview (second column), capture/rename, manual controls."""

from __future__ import annotations

import time

import streamlit as st

from config.loader import load_settings
from ui.adb_reference_shot import capture_reference_adb
from ui.settings_state import ensure_ui_settings_session_defaults, get_ui_adb_bin
from ui.preview_display import png_bytes_fitted
from ui.redis_client import (
    fetch_fsm_history,
    push_instance_command,
    require_redis_connection,
)
from ui.reference_preview import (
    load_reference_preview,
    rename_reference_to_basename,
    resolve_rename_source_path,
)

st.title("Instance")

flash_key_inst = "instance_rename_flash"
if flash_key_inst in st.session_state:
    st.success(st.session_state.pop(flash_key_inst))

ensure_ui_settings_session_defaults()

settings = load_settings()
client = require_redis_connection()

params = st.query_params
instance_id = params.get("instance_id", settings.instances[0].instance_id if settings.instances else "")

choices = [i.instance_id for i in settings.instances]
if not choices:
    st.warning("No instances in config.")
    st.stop()

if instance_id not in choices:
    instance_id = choices[0]

instance_id = st.selectbox("Instance", choices, index=choices.index(instance_id))

inst_cfg = next(i for i in settings.instances if i.instance_id == instance_id)

col_left, col_right = st.columns([2, 3], gap="large")

immediate_detail: bytes | None = None

with col_left:
    ref_name = st.text_input(
        "references/ basename",
        value="",
        placeholder="main_city",
        key=f"refname-detail-{instance_id}",
        help=(
            "Without .png. If empty: `temporal/{instance_id}_current_state` (overwritten each capture)."
        ).replace("{instance_id}", instance_id),
    )

    cap_c1, cap_c2 = st.columns(2)
    with cap_c1:
        cap_click = st.button("Screenshot → references/", key=f"cap-{instance_id}")
    with cap_c2:
        ren_click = st.button(
            "Rename file → basename",
            key=f"ren-{instance_id}",
            help="Renames the current PNG on disk to `<basename>.png`.",
        )

    if cap_click:
        png, fname, err = capture_reference_adb(inst_cfg, ref_name, adb_bin=get_ui_adb_bin())
        if err:
            st.error(err)
        else:
            immediate_detail = png
            st.success(f"references/{fname}")

    if ren_click:
        src = resolve_rename_source_path(instance_id, ref_name, None)
        if src is None:
            st.warning("Nothing to rename — set basename to an existing file or use latest capture naming.")
        else:
            ok, msg = rename_reference_to_basename(src, ref_name, instance_id)
            if ok:
                st.session_state[flash_key_inst] = msg
                st.rerun()
            else:
                st.error(msg)

    st.subheader("Manual controls")

    if not inst_cfg.player_ids:
        st.warning("No **player_ids** for this instance in config — add at least one for switch/task controls.")
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
            task_player = st.selectbox("Player for task", inst_cfg.player_ids, key="tp")
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

    st.subheader("FSM history (per player)")
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
    st.subheader("Preview from references/")
    max_side = st.slider(
        "Preview scale — max longer side (px)",
        min_value=320,
        max_value=1400,
        value=720,
        step=40,
        key=f"detail_preview_max_{instance_id}",
        help="Display only; file on disk unchanged. Preview uses this pixel size (not stretched to column width).",
    )

    img_bytes: bytes | None = immediate_detail
    ref_cap = ""
    if img_bytes is None:
        img_bytes, ref_cap = load_reference_preview(instance_id, ref_name)

    if img_bytes is not None:
        fitted, native, shown = png_bytes_fitted(img_bytes, max_side)
        cap_txt = f"{instance_id} (ADB)" if immediate_detail is not None else f"{ref_cap} · {instance_id}"
        st.image(
            fitted,
            caption=(
                f"{cap_txt} · display ~{shown[0]}×{shown[1]} px "
                f"(native {native[0]}×{native[1]})"
            ),
            # Natural pixel size so "max longer side" is visible; container-width would stretch every scale to the column.
            use_container_width=False,
        )
    else:
        st.info(
            f"No file yet for this basename or no auto shots matching `{instance_id}_*.png`. "
            "Use **Screenshot → references/** (ADB serial = **bluestacks_window_title**)."
        )
