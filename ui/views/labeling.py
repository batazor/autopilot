"""Reference screenshots: file tree, basename/rename, one canvas for regions bound to the selected PNG."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from config.loader import load_settings
from config.reference_naming import TEMPORAL_SUBDIR
from ui.adb_reference_shot import capture_reference_adb
from ui.area_annotator import REPO_ROOT, export_region_crops, render_area_annotator_ui
from ui.labeling_reference_panel import LABELING_BN_SYNC_SEL, labeling_basename_widget_key, labeling_resolve_sel
from ui.reference_preview import list_reference_pngs, references_root
from ui.settings_state import ensure_ui_settings_session_defaults, get_ui_adb_bin

if "labeling_rename_flash" in st.session_state:
    st.success(st.session_state.pop("labeling_rename_flash"))

ensure_ui_settings_session_defaults()

settings = load_settings()
instances = settings.instances
if not instances:
    st.warning("No instances in config.")
    st.stop()

inst_ids = [i.instance_id for i in instances]

instance_id = st.selectbox(
    "Instance (ADB)",
    inst_ids,
    key="labeling_instance",
    help="Used for **New screenshot** and sanitized basename.",
)
inst_cfg = next(i for i in instances if i.instance_id == instance_id)

_last_inst_key = "labeling_bn_last_instance"
if st.session_state.get(_last_inst_key) != instance_id:
    st.session_state[_last_inst_key] = instance_id
    st.session_state.pop(LABELING_BN_SYNC_SEL, None)

new_screenshot = False
write_crops = False

hdr_title, hdr_btn = st.columns([3, 1])
with hdr_title:
    st.markdown("# Labeling")
with hdr_btn:
    hdr_cap, hdr_crop = st.columns(2, gap="small")
    with hdr_cap:
        new_screenshot = st.button(
            "New screenshot",
            type="primary",
            width="stretch",
            key="labeling_header_capture",
            help="ADB capture into references/ using basename below (empty → preview snapshot name).",
        )
    with hdr_crop:
        write_crops = st.button(
            "Write crops",
            type="secondary",
            width="stretch",
            key="labeling_header_crops",
            help="Save bbox crops under **references/crop/** for the current reference entry.",
        )

ref_root = references_root()
existing = list_reference_pngs(exclude_temporal=True, exclude_crop=True)

render_area_annotator_ui(
    labeling_mode=True,
    labeling_ref_root=ref_root,
    labeling_existing=existing,
    labeling_instance_id=instance_id,
)

sel = labeling_resolve_sel(ref_root, existing)
basename_for_capture = str(
    st.session_state.get(labeling_basename_widget_key(sel if existing else None)) or ""
)

if new_screenshot:
    _png, fname, err = capture_reference_adb(
        inst_cfg, basename_for_capture, adb_bin=get_ui_adb_bin()
    )
    if err:
        st.error(err)
    else:
        if fname.startswith(f"{TEMPORAL_SUBDIR}/"):
            pick = existing[0].relative_to(ref_root).as_posix() if existing else None
            if pick:
                st.session_state["labeling_tree_selection"] = pick
                st.session_state[LABELING_BN_SYNC_SEL] = pick
                st.session_state[labeling_basename_widget_key(pick)] = Path(pick).stem
            else:
                st.session_state.pop("labeling_tree_selection", None)
                st.session_state.pop(LABELING_BN_SYNC_SEL, None)
        else:
            st.session_state["labeling_tree_selection"] = fname
            st.session_state[LABELING_BN_SYNC_SEL] = fname
            st.session_state[labeling_basename_widget_key(fname)] = Path(fname).stem
        st.session_state["labeling_rename_flash"] = f"Saved **references/{fname}**"
        st.rerun()

if write_crops:
    pil = st.session_state.get("pil_original")
    doc = st.session_state.get("area_doc")
    ei = int(st.session_state.get("entry_idx", -1))
    entries = (doc or {}).get("screens") or []
    if pil is None or doc is None:
        st.error("Load a reference image first — pick a PNG in the right column or use **New screenshot**.")
    elif ei < 0 or ei >= len(entries):
        st.error("No **area.json** entry for this reference.")
    else:
        ref_raw = entries[ei].get("ocr")
        regions = entries[ei].get("regions") or []
        ref_s = str(ref_raw).strip() if ref_raw else ""
        bbox_n = sum(1 for r in regions if r.get("bbox"))
        if not ref_s:
            st.error("Current entry has no reference (**ocr**) path.")
        elif bbox_n == 0:
            st.warning("No regions with a bbox — draw regions on the canvas first.")
        else:
            prog = st.progress(0)
            try:
                outs = export_region_crops(
                    pil,
                    ref_s,
                    regions,
                    progress=lambda x: prog.progress(x),
                )
                rels = [o.relative_to(REPO_ROOT).as_posix() for o in outs]
                if rels:
                    st.success("Saved:\n" + "\n".join(f"- `{p}`" for p in rels))
                else:
                    st.warning("Nothing written.")
            except (OSError, ValueError) as e:
                st.error(str(e))
            finally:
                prog.empty()
