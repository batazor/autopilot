"""Reference file tree + basename (Labeling page, column embedded in annotator)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import streamlit as st
from st_ant_tree import st_ant_tree

from config.reference_naming import TEMPORAL_SUBDIR, reference_file_basename
from ui.keys import (
    AREA_DOC,
    CANVAS_LAST_SIG,
    CANVAS_REV,
    LABELING_BN_NONE,
    LABELING_BN_SYNC_SEL,
    LABELING_TEMPORAL_REGIONS,
    LABELING_PENDING_CAPTURE_REL,
    LABELING_RENAME_FLASH,
    LABELING_REF_TREE_NONCE,
    LABELING_SELECTION_BEFORE_CAPTURE,
    LABELING_TREE_SELECTION,
)
from ui.reference_area_sync import sync_area_json_ocr_after_reference_rename
from ui.reference_preview import move_temporal_to_reference_basename, rename_reference_to_basename
from ui.reference_tree import (
    build_reference_dir_tree,
    build_reference_screen_id_tree_data,
    dir_node_to_ant_tree_data,
)


def labeling_basename_widget_key(sel: str | None) -> str:
    if not sel:
        return LABELING_BN_NONE
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
    """Resolve ``LABELING_TREE_SELECTION`` after the reference column rendered."""
    raw = st.session_state.get(LABELING_TREE_SELECTION)
    if isinstance(raw, str) and raw and (ref_root / raw).is_file():
        return raw
    if not existing:
        return None
    default_rel = existing[0].relative_to(ref_root).as_posix()
    st.session_state[LABELING_TREE_SELECTION] = default_rel
    return default_rel


def labeling_forced_reference_rel(sel: str | None, existing: list[Path]) -> str | None:
    if not sel:
        return None
    return (Path("references") / sel).as_posix()


def render_labeling_reference_column(
    ref_root: Path,
    existing: list[Path],
    instance_id: str,
) -> None:
    """Second column: reference PNG tree + basename / rename."""
    with st.expander("Reference image", expanded=True):
        group_by_sid = st.toggle(
            "Group by Screen ID",
            value=True,
            help="Group references by `screen_id` from `area.json` instead of directory structure.",
            key="labeling_ref_group_by_sid",
        )
        if existing:
            default_rel = existing[0].relative_to(ref_root).as_posix()
            stored_raw = st.session_state.get(LABELING_TREE_SELECTION)
            is_temporal_sel = (
                isinstance(stored_raw, str)
                and stored_raw
                and (
                    stored_raw == TEMPORAL_SUBDIR
                    or stored_raw.startswith(f"{TEMPORAL_SUBDIR}/")
                )
            )

            # Tree can only show non-temporal refs, but selection may be a pending
            # `references/temporal/...` capture. In that case, keep the selection as-is
            # and only use `default_rel` as the tree's defaultValue.
            stored_for_tree = default_rel
            if not is_temporal_sel and isinstance(stored_raw, str) and stored_raw.strip():
                if (ref_root / stored_raw).is_file():
                    stored_for_tree = stored_raw
                else:
                    st.session_state[LABELING_TREE_SELECTION] = default_rel
            elif not is_temporal_sel and not stored_raw:
                st.session_state[LABELING_TREE_SELECTION] = default_rel

            if group_by_sid:
                tree_data = build_reference_screen_id_tree_data(
                    existing, ref_root, st.session_state.get(AREA_DOC)
                )
            else:
                tree_data = dir_node_to_ant_tree_data(build_reference_dir_tree(existing, ref_root))
            tree_nonce = int(st.session_state.get(LABELING_REF_TREE_NONCE, 0))
            picked = st_ant_tree(
                treeData=tree_data,
                treeCheckable=False,
                multiple=False,
                showSearch=True,
                placeholder="Select references/*.png",
                defaultValue=[stored_for_tree],
                width_dropdown="100%",
                max_height=380,
                treeLine=True,
                only_children_select=True,
                allowClear=False,
                key=f"labeling_ref_ant_tree_{tree_nonce}",
            )

            sel = stored_raw if isinstance(stored_raw, str) else None
            one = _ant_tree_single_value(picked)
            if one and (ref_root / one).is_file():
                # When a pending temporal capture is active, the tree still renders with a
                # non-temporal defaultValue. AntTree will often emit that default on rerun
                # even without user interaction — do not treat it as an explicit selection.
                if not is_temporal_sel or one != stored_for_tree:
                    sel = one
            if not sel or not (ref_root / sel).is_file():
                sel = stored_for_tree
            # Only overwrite selection when it is a real file under references/.
            # Pending temporal selection is kept unless user explicitly picks another ref.
            st.session_state[LABELING_TREE_SELECTION] = sel

        else:
            st.session_state.pop(LABELING_BN_SYNC_SEL, None)

        sel_out = st.session_state.get(LABELING_TREE_SELECTION) if existing else None
        bn_key = labeling_basename_widget_key(sel_out if existing else None)

        if existing and sel_out:
            synced_for = st.session_state.get(LABELING_BN_SYNC_SEL)
            if synced_for != sel_out:
                st.session_state[LABELING_BN_SYNC_SEL] = sel_out
                st.session_state[bn_key] = Path(sel_out).stem
            elif bn_key not in st.session_state:
                st.session_state[bn_key] = Path(sel_out).stem
        elif not existing:
            st.session_state.setdefault(LABELING_BN_NONE, "")

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
                        "From selection; **Enter** or **💾** renames on disk. "
                        "**New screenshot** uses its own unique name."
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
                # Pending capture workflow: New screenshot writes to `references/temporal/`;
                # assigning a basename "publishes" it into `references/`.
                pending_rel = st.session_state.get(LABELING_PENDING_CAPTURE_REL)
                if isinstance(pending_rel, str) and pending_rel.startswith(f"{TEMPORAL_SUBDIR}/"):
                    src_temporal = ref_root / pending_rel.replace("\\", "/")
                    ok, msg, new_rel = move_temporal_to_reference_basename(
                        src_temporal=src_temporal,
                        name_input=name_raw,
                        instance_id=instance_id,
                    )
                    if ok and new_rel:
                        # Promote in-memory temporal regions to the new persistent ref entry.
                        try:
                            from ui.area_annotator import ensure_entry_for_reference_path

                            doc = st.session_state.get(AREA_DOC)
                            if isinstance(doc, dict):
                                entries = doc.get("screens")
                                if isinstance(entries, list):
                                    ocr = (Path("references") / new_rel).as_posix()
                                    ei = ensure_entry_for_reference_path(entries, ocr)
                                    regs = st.session_state.get(LABELING_TEMPORAL_REGIONS)
                                    if isinstance(regs, list):
                                        entries[ei]["regions"] = regs  # type: ignore[index]
                                    st.session_state.entry_idx = ei
                        except Exception:
                            # Best-effort; UI still works even if regions can't be promoted.
                            pass
                        st.session_state.pop(LABELING_TEMPORAL_REGIONS, None)
                        st.session_state.pop(LABELING_PENDING_CAPTURE_REL, None)
                        st.session_state.pop(LABELING_SELECTION_BEFORE_CAPTURE, None)
                        st.session_state[LABELING_TREE_SELECTION] = new_rel
                        st.session_state[LABELING_BN_SYNC_SEL] = new_rel
                        st.session_state[labeling_basename_widget_key(new_rel)] = Path(new_rel).stem
                        st.session_state[LABELING_RENAME_FLASH] = msg
                        st.session_state[LABELING_REF_TREE_NONCE] = (
                            int(st.session_state.get(LABELING_REF_TREE_NONCE, 0)) + 1
                        )
                        try:
                            st.query_params["ref"] = new_rel
                        except Exception:
                            pass
                        st.rerun()
                    st.error(msg) if not ok else None
                    if not ok:
                        # Keep the user in place to try another basename.
                        st.stop()

                dest_base = reference_file_basename(name_raw, instance_id)
                if Path(sel_out).stem == dest_base:
                    st.info("Name unchanged.")
                else:
                    src = ref_root / sel_out
                    ok, msg = rename_reference_to_basename(src, name_raw, instance_id)
                    if ok:
                        new_rel = f"{dest_base}.png"
                        repo_rt = ref_root.parent
                        sync_ok, sync_err, n_ocr = sync_area_json_ocr_after_reference_rename(
                            repo_rt,
                            old_rel_under_refs=sel_out.replace("\\", "/"),
                            new_rel_under_refs=new_rel.replace("\\", "/"),
                        )
                        flash = msg
                        if sync_ok and n_ocr:
                            flash += f" · Updated **area.json** (**{n_ocr}** ``ocr`` path(s))."
                        elif not sync_ok and sync_err:
                            # PNG is already renamed — roll it back to keep disk + area.json in sync.
                            renamed_path = ref_root / new_rel
                            try:
                                renamed_path.rename(ref_root / sel_out)
                                flash = f"Rename rolled back — **area.json** sync failed: {sync_err}"
                            except OSError as rollback_exc:
                                flash = (
                                    f"Renamed to `{new_rel}` but **area.json** sync failed: {sync_err} "
                                    f"(rollback also failed: {rollback_exc})"
                                )
                            st.error(flash)
                            st.rerun()
                        st.session_state[LABELING_TREE_SELECTION] = new_rel
                        st.session_state[LABELING_BN_SYNC_SEL] = new_rel
                        st.session_state[labeling_basename_widget_key(new_rel)] = dest_base
                        st.session_state[LABELING_RENAME_FLASH] = flash
                        st.session_state[LABELING_REF_TREE_NONCE] = (
                            int(st.session_state.get(LABELING_REF_TREE_NONCE, 0)) + 1
                        )
                        try:
                            from ui.area_annotator import AREA_JSON_PATH, load_json

                            st.session_state.area_doc = load_json(AREA_JSON_PATH)
                            st.session_state[CANVAS_REV] = (
                                int(st.session_state.get(CANVAS_REV, 0)) + 1
                            )
                            st.session_state[CANVAS_LAST_SIG] = ""
                        except (OSError, ValueError):
                            pass
                        st.rerun()
                    else:
                        st.error(msg)

        if not existing:
            st.info("No PNGs yet — use **New screenshot**.")


def purge_reference_png_and_area_entries(repo_root: Path, ref_root: Path, rel_posix: str) -> None:
    """Delete PNG under ``references/`` and drop matching ``area_doc`` screen rows."""
    rel_posix = rel_posix.replace("\\", "/").strip()
    if not rel_posix or rel_posix.startswith("..") or "/.." in rel_posix:
        return
    path = ref_root / rel_posix
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass
    try:
        target = (repo_root / "references" / rel_posix).resolve()
    except OSError:
        return
    doc = st.session_state.get(AREA_DOC)
    if not isinstance(doc, dict):
        return
    entries = doc.get("screens")
    if not isinstance(entries, list):
        return
    kept: list = []
    for e in entries:
        if not isinstance(e, dict):
            kept.append(e)
            continue
        raw = str(e.get("ocr") or "").strip()
        if not raw:
            kept.append(e)
            continue
        p = Path(raw)
        if not p.is_absolute():
            p = repo_root / p
        try:
            if p.resolve() == target:
                continue
        except OSError:
            pass
        kept.append(e)
    doc["screens"] = kept
