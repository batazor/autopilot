"""Reference file tree + basename (Labeling page, column embedded in annotator)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st
from st_ant_tree import st_ant_tree

from config.reference_naming import TEMPORAL_SUBDIR, reference_file_basename
from ui.reference_preview import rename_reference_to_basename
from ui.reference_tree import build_reference_dir_tree, dir_node_to_ant_tree_data

LABELING_BN_SYNC_SEL = "labeling_basename_sync_sel"


def labeling_basename_widget_key(sel: str | None) -> str:
    if not sel:
        return "labeling_bn_none"
    digest = hashlib.sha256(sel.encode("utf-8")).hexdigest()[:20]
    return f"labeling_bn_{digest}"


def _ant_tree_single_value(picked: object) -> str | None:
    if picked is None:
        return None
    if isinstance(picked, str):
        p = picked.strip()
        return p if p and not p.startswith("__dir__") else None
    if isinstance(picked, (list, tuple)) and len(picked) > 0:
        v = picked[0]
        if isinstance(v, str) and v and not v.startswith("__dir__"):
            return v
    return None


def labeling_resolve_sel(ref_root: Path, existing: list[Path]) -> str | None:
    """Resolve ``labeling_tree_selection`` after the reference column rendered."""
    if not existing:
        return None
    raw = st.session_state.get("labeling_tree_selection")
    if isinstance(raw, str) and raw and (ref_root / raw).is_file():
        return raw
    default_rel = existing[0].relative_to(ref_root).as_posix()
    st.session_state["labeling_tree_selection"] = default_rel
    return default_rel


def labeling_forced_reference_rel(sel: str | None, existing: list[Path]) -> str | None:
    if not sel or not existing:
        return None
    if sel == TEMPORAL_SUBDIR or sel.startswith(f"{TEMPORAL_SUBDIR}/"):
        return None
    return (Path("references") / sel).as_posix()


def render_labeling_reference_column(
    ref_root: Path,
    existing: list[Path],
    instance_id: str,
) -> None:
    """Second column: reference PNG tree + basename / rename."""
    with st.expander("Reference image", expanded=True):
        if existing:
            default_rel = existing[0].relative_to(ref_root).as_posix()
            stored = st.session_state.get("labeling_tree_selection")
            if stored:
                if stored == TEMPORAL_SUBDIR or stored.startswith(f"{TEMPORAL_SUBDIR}/"):
                    stored = None
            if not stored or not (ref_root / stored).is_file():
                stored = default_rel
                st.session_state["labeling_tree_selection"] = stored

            tree_data = dir_node_to_ant_tree_data(build_reference_dir_tree(existing, ref_root))
            picked = st_ant_tree(
                treeData=tree_data,
                treeCheckable=False,
                multiple=False,
                showSearch=True,
                placeholder="Select references/*.png",
                defaultValue=[stored],
                width_dropdown="100%",
                max_height=380,
                treeLine=True,
                only_children_select=True,
                allowClear=False,
                key="labeling_ref_ant_tree",
            )

            sel = stored
            one = _ant_tree_single_value(picked)
            if one:
                sel = one
            if not sel or not (ref_root / sel).is_file():
                sel = default_rel
            st.session_state["labeling_tree_selection"] = sel

        else:
            st.session_state.pop(LABELING_BN_SYNC_SEL, None)

        sel_out = st.session_state.get("labeling_tree_selection") if existing else None
        bn_key = labeling_basename_widget_key(sel_out if existing else None)

        if existing and sel_out:
            synced_for = st.session_state.get(LABELING_BN_SYNC_SEL)
            if synced_for != sel_out:
                st.session_state[LABELING_BN_SYNC_SEL] = sel_out
                st.session_state[bn_key] = Path(sel_out).stem
            elif bn_key not in st.session_state:
                st.session_state[bn_key] = Path(sel_out).stem
        elif not existing:
            st.session_state.setdefault("labeling_bn_none", "")

        with st.form(
            "labeling_basename_form",
            clear_on_submit=False,
            enter_to_submit=True,
            border=False,
        ):
            st.markdown("**Basename**")
            bn_col, _submit_col = st.columns([16, 1], gap="small", vertical_alignment="bottom")
            with bn_col:
                st.text_input(
                    "basename_value",
                    key=bn_key,
                    label_visibility="collapsed",
                    placeholder="without .png",
                    help=(
                        "Filled from the selected file. Press **Enter** or click **💾** to rename on disk. "
                        "**New screenshot** uses this field too (empty → preview snapshot name)."
                    ),
                )
            with _submit_col:
                submit_rename = st.form_submit_button(
                    "💾",
                    type="tertiary",
                    width="content",
                    help="Apply basename rename on disk (Enter in the field does the same).",
                    key="labeling_basename_submit",
                )

        if submit_rename:
            name_raw = str(st.session_state.get(bn_key) or "").strip()
            if not existing or not sel_out:
                st.warning("Nothing selected.")
            elif not name_raw:
                st.warning("Basename cannot be empty.")
            else:
                dest_base = reference_file_basename(name_raw, instance_id)
                if Path(sel_out).stem == dest_base:
                    st.info("Name unchanged.")
                else:
                    src = ref_root / sel_out
                    ok, msg = rename_reference_to_basename(src, name_raw, instance_id)
                    if ok:
                        new_rel = f"{dest_base}.png"
                        st.session_state["labeling_tree_selection"] = new_rel
                        st.session_state[LABELING_BN_SYNC_SEL] = new_rel
                        st.session_state[labeling_basename_widget_key(new_rel)] = dest_base
                        st.session_state["labeling_rename_flash"] = msg
                        st.rerun()
                    else:
                        st.error(msg)

        if not existing:
            st.info("No PNGs yet — use **New screenshot**.")
