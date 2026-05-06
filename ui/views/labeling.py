"""Labeling: reference tree, basename/rename, canvas bound to the selected PNG."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from config.loader import load_settings
from config.reference_naming import TEMPORAL_SUBDIR, unique_label_capture_basename
from ui.adb_reference_shot import capture_reference_adb
from ui.area_annotator import (
    REPO_ROOT,
    ensure_entry_for_reference_path,
    export_all_region_crops_for_area_doc,
    render_area_annotator_ui,
)
from ui.labeling_reference_panel import (
    LABELING_BN_SYNC_SEL,
    LABELING_PENDING_CAPTURE_REL,
    LABELING_REF_TREE_NONCE,
    LABELING_SELECTION_BEFORE_CAPTURE,
    labeling_basename_widget_key,
    labeling_resolve_sel,
    purge_reference_png_and_area_entries,
)
from ui.reference_preview import list_reference_pngs, references_root
from ui.settings_state import ensure_ui_settings_session_defaults, get_ui_adb_bin


def _handle_discard_pending_capture(*, ref_root: Path) -> None:
    rel = st.session_state.pop(LABELING_PENDING_CAPTURE_REL, None)
    prev = st.session_state.pop(LABELING_SELECTION_BEFORE_CAPTURE, None)
    if not rel:
        return
    purge_reference_png_and_area_entries(REPO_ROOT, ref_root, rel)

    existing2 = list_reference_pngs(exclude_temporal=True, exclude_crop=True)
    restored: str | None
    if prev and (ref_root / prev).is_file():
        restored = prev
        st.session_state["labeling_tree_selection"] = prev
        st.session_state[LABELING_BN_SYNC_SEL] = prev
        st.session_state[labeling_basename_widget_key(prev)] = Path(prev).stem
    elif existing2:
        restored = existing2[0].relative_to(ref_root).as_posix()
        st.session_state["labeling_tree_selection"] = restored
        st.session_state[LABELING_BN_SYNC_SEL] = restored
        st.session_state[labeling_basename_widget_key(restored)] = Path(restored).stem
    else:
        restored = None
        st.session_state.pop("labeling_tree_selection", None)
        st.session_state.pop(LABELING_BN_SYNC_SEL, None)

    doc = st.session_state.area_doc
    entries = doc.get("screens")
    if not isinstance(entries, list):
        entries = []
        doc["screens"] = entries
    if restored:
        ocr_norm = (Path("references") / restored).as_posix()
        st.session_state.entry_idx = ensure_entry_for_reference_path(entries, ocr_norm)
    else:
        st.session_state.entry_idx = 0 if entries else -1

    st.session_state[LABELING_REF_TREE_NONCE] = (
        int(st.session_state.get(LABELING_REF_TREE_NONCE, 0)) + 1
    )
    st.session_state.canvas_rev = int(st.session_state.get("canvas_rev", 0)) + 1
    st.session_state.last_canvas_sig = ""
    st.session_state["labeling_rename_flash"] = f"Removed **references/{rel}** (unsaved capture)."


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
    help="ADB device for captures; **New screenshot** writes a new unique file under references/.",
)
inst_cfg = next(i for i in instances if i.instance_id == instance_id)

_last_inst_key = "labeling_bn_last_instance"
if st.session_state.get(_last_inst_key) != instance_id:
    st.session_state[_last_inst_key] = instance_id
    st.session_state.pop(LABELING_BN_SYNC_SEL, None)

ref_root = references_root()
existing = list_reference_pngs(exclude_temporal=True, exclude_crop=True)

new_screenshot = False
write_crops = False
discard_capture = False

hdr_title, hdr_btn = st.columns([3, 1])
with hdr_title:
    st.markdown("# Labeling")
with hdr_btn:
    hdr_cap, hdr_discard, hdr_crop = st.columns(3, gap="small")
    with hdr_cap:
        new_screenshot = st.button(
            "New screenshot",
            type="primary",
            width="stretch",
            key="labeling_header_capture",
            help=(
                "ADB → new ``references/<instance>_shot_<time>_<rand>.png``; "
                "does not overwrite the selected file."
            ),
        )
    pending_rel = st.session_state.get(LABELING_PENDING_CAPTURE_REL)
    can_discard = (
        isinstance(pending_rel, str)
        and pending_rel != ""
        and not pending_rel.startswith("..")
        and (ref_root / pending_rel).is_file()
    )
    with hdr_discard:
        discard_capture = st.button(
            "Discard screenshot",
            type="secondary",
            width="stretch",
            key="labeling_header_discard",
            disabled=not can_discard,
            help="Delete the last **New screenshot** file and drop its in-memory area.json row "
            "if you have not saved yet. Cleared automatically after **Save area.json**.",
        )
    with hdr_crop:
        write_crops = st.button(
            "Write crops",
            type="secondary",
            width="stretch",
            key="labeling_header_crops",
            help=(
                "Save bbox crops under **references/crop/** for **every** ``area.json`` screen "
                "whose ``ocr`` PNG exists (skips ``overlay_auxiliary`` regions and missing files)."
            ),
        )

if discard_capture:
    _handle_discard_pending_capture(ref_root=ref_root)
    st.rerun()

render_area_annotator_ui(
    labeling_mode=True,
    labeling_ref_root=ref_root,
    labeling_existing=existing,
    labeling_instance_id=instance_id,
)

sel = labeling_resolve_sel(ref_root, existing)

if new_screenshot:
    capture_bn = unique_label_capture_basename(instance_id)
    _png, fname, err = capture_reference_adb(
        inst_cfg, capture_bn, adb_bin=get_ui_adb_bin()
    )
    if err:
        st.error(err)
    else:
        old_pending = st.session_state.get(LABELING_PENDING_CAPTURE_REL)
        if (
            isinstance(old_pending, str)
            and old_pending
            and old_pending != fname
            and not old_pending.startswith("..")
        ):
            purge_reference_png_and_area_entries(REPO_ROOT, ref_root, old_pending)

        raw_prev = st.session_state.get("labeling_tree_selection")
        prev_ok = (
            raw_prev
            if isinstance(raw_prev, str) and (ref_root / raw_prev).is_file()
            else None
        )
        if fname.startswith(f"{TEMPORAL_SUBDIR}/"):
            pick = existing[0].relative_to(ref_root).as_posix() if existing else None
            if pick:
                st.session_state["labeling_tree_selection"] = pick
                st.session_state[LABELING_BN_SYNC_SEL] = pick
                st.session_state[labeling_basename_widget_key(pick)] = Path(pick).stem
            else:
                st.session_state.pop("labeling_tree_selection", None)
                st.session_state.pop(LABELING_BN_SYNC_SEL, None)
            st.session_state.pop(LABELING_PENDING_CAPTURE_REL, None)
            st.session_state.pop(LABELING_SELECTION_BEFORE_CAPTURE, None)
        else:
            st.session_state[LABELING_SELECTION_BEFORE_CAPTURE] = prev_ok
            st.session_state[LABELING_PENDING_CAPTURE_REL] = fname
            st.session_state["labeling_tree_selection"] = fname
            st.session_state[LABELING_BN_SYNC_SEL] = fname
            st.session_state[labeling_basename_widget_key(fname)] = Path(fname).stem
        st.session_state[LABELING_REF_TREE_NONCE] = (
            int(st.session_state.get(LABELING_REF_TREE_NONCE, 0)) + 1
        )
        st.session_state["labeling_rename_flash"] = f"Saved **references/{fname}**"
        st.rerun()

if write_crops:
    doc = st.session_state.get("area_doc")
    if doc is None:
        st.error("No area document loaded.")
    else:
        prog = st.progress(0)
        try:
            written, warns = export_all_region_crops_for_area_doc(
                doc,
                repo_root=REPO_ROOT,
                progress=lambda x: prog.progress(x),
            )
            rels = [p.relative_to(REPO_ROOT).as_posix() for p in written]
            if rels:
                preview = "\n".join(f"- `{p}`" for p in rels[:80])
                more = f"\n… and **{len(rels) - 80}** more." if len(rels) > 80 else ""
                st.success(f"Wrote **{len(rels)}** crop(s):\n{preview}{more}")
            else:
                st.warning("No crops written — check reference PNG paths and non-auxiliary regions.")
            if warns:
                with st.expander("Warnings", expanded=False):
                    st.markdown("\n".join(f"- {w}" for w in warns))
        except (OSError, ValueError) as e:
            st.error(str(e))
        finally:
            prog.empty()
