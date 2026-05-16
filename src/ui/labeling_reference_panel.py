"""Reference file tree + basename (Labeling page, column embedded in annotator)."""
from __future__ import annotations

import contextlib
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
    LABELING_PENDING_CAPTURE_REL,
    LABELING_REF_TREE_NONCE,
    LABELING_RENAME_FLASH,
    LABELING_SELECTION_BEFORE_CAPTURE,
    LABELING_TEMPORAL_REGIONS,
    LABELING_TREE_SELECTION,
)
from ui.labeling_helpers import (
    build_reference_leaf_meta_index,
    preview_delete_reference_impact,
    suggest_basename_from_entry,
)
from ui.reference_area_sync import sync_area_json_ocr_after_reference_rename
from ui.reference_preview import move_temporal_to_reference_basename, rename_reference_to_basename
from ui.reference_tree import (
    build_reference_dir_tree,
    build_reference_screen_id_tree_data,
    dir_node_to_ant_tree_data,
    temporal_capture_tree_node,
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


def labeling_forced_reference_rel(
    sel: str | None,
    existing: list[Path],
    *,
    references_prefix: str = "references",
) -> str | None:
    if not sel:
        return None
    prefix = references_prefix.strip().rstrip("/")
    return f"{prefix}/{sel}".replace("\\", "/")


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

            area_doc = st.session_state.get(AREA_DOC)
            if group_by_sid:
                tree_data = build_reference_screen_id_tree_data(
                    existing, ref_root, area_doc
                )
            else:
                meta_by_rel = build_reference_leaf_meta_index(area_doc, ref_root)
                tree_data = dir_node_to_ant_tree_data(
                    build_reference_dir_tree(existing, ref_root),
                    meta_by_rel,
                )
            if is_temporal_sel and isinstance(stored_raw, str) and stored_raw.strip():
                tree_data = [temporal_capture_tree_node(stored_raw)] + tree_data
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
            picked_new_ref = False
            # When a pending temporal capture is active, the tree still renders with a
            # non-temporal defaultValue. AntTree will often emit that default on rerun
            # even without user interaction — do not treat it as an explicit selection.
            if (
                one
                and (ref_root / one).is_file()
                and (not is_temporal_sel or one != stored_for_tree)
            ):
                sel = one
                picked_new_ref = one != stored_raw
            if not sel or not (ref_root / sel).is_file():
                sel = stored_for_tree
            # Only overwrite selection when it is a real file under references/.
            # Pending temporal selection is kept unless user explicitly picks another ref.
            st.session_state[LABELING_TREE_SELECTION] = sel
            if picked_new_ref:
                st.session_state["_labeling_last_ref_param"] = sel
                st.session_state[CANVAS_REV] = int(st.session_state.get(CANVAS_REV, 0)) + 1
                st.session_state[CANVAS_LAST_SIG] = ""
                with contextlib.suppress(Exception):
                    st.query_params["ref"] = sel
                    if "version" in st.query_params:
                        del st.query_params["version"]
                st.rerun()

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

        if existing and sel_out:
            entries_bn = (
                st.session_state.get(AREA_DOC, {}).get("screens")
                if isinstance(st.session_state.get(AREA_DOC), dict)
                else None
            )
            entry_bn: dict | None = None
            ei_bn = int(st.session_state.get("entry_idx", -1))
            if isinstance(entries_bn, list) and 0 <= ei_bn < len(entries_bn):
                cand_e = entries_bn[ei_bn]
                if isinstance(cand_e, dict):
                    entry_bn = cand_e
            suggested = suggest_basename_from_entry(entry_bn, instance_id)
            if suggested and suggested != Path(sel_out).stem:
                hint_col, use_col = st.columns([3, 1], gap="small", vertical_alignment="center")
                with hint_col:
                    st.caption(f"Suggested from **screen_id**: `{suggested}`")
                with use_col:
                    if st.button(
                        "Use",
                        key=f"labeling_use_suggest_{labeling_basename_widget_key(sel_out)}",
                        width="stretch",
                        help="Fill basename field with the suggested name",
                    ):
                        st.session_state[labeling_basename_widget_key(sel_out)] = suggested
                        st.rerun()

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
                        references_dir=ref_root,
                    )
                    if ok and new_rel:
                        # Promote in-memory temporal regions to the new persistent ref entry.
                        try:
                            from ui.area_annotator import ensure_entry_for_reference_path

                            doc = st.session_state.get(AREA_DOC)
                            if isinstance(doc, dict):
                                entries = doc.get("screens")
                                if isinstance(entries, list):
                                    from ui.wiki_module import active_references_prefix

                                    ocr = f"{active_references_prefix()}/{new_rel}".replace(
                                        "\\", "/"
                                    )
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
                        with contextlib.suppress(Exception):
                            st.query_params["ref"] = new_rel
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
                    ok, msg = rename_reference_to_basename(
                        src, name_raw, instance_id, references_dir=ref_root
                    )
                    if ok:
                        new_rel = f"{dest_base}.png"
                        from ui.area_annotator import REPO_ROOT
                        from ui.wiki_module import active_references_prefix, active_wiki_area_path

                        sync_ok, sync_err, n_ocr = sync_area_json_ocr_after_reference_rename(
                            REPO_ROOT,
                            old_rel_under_refs=sel_out.replace("\\", "/"),
                            new_rel_under_refs=new_rel.replace("\\", "/"),
                            area_path=active_wiki_area_path(),
                            references_prefix=active_references_prefix(),
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
                            from ui.area_annotator import load_json
                            from ui.wiki_module import active_wiki_area_path

                            st.session_state.area_doc = load_json(active_wiki_area_path())
                            st.session_state[CANVAS_REV] = (
                                int(st.session_state.get(CANVAS_REV, 0)) + 1
                            )
                            st.session_state[CANVAS_LAST_SIG] = ""
                        except (OSError, ValueError):
                            pass
                        st.rerun()
                    else:
                        st.error(msg)

        # Destructive: PNG + every area.json entry that references it + per-region crop tiles.
        # Two-step confirm so a misclick can't nuke a reference; the second click in the same
        # rerun cycle commits the delete. The container key drives a CSS rule below so the
        # button reads as red without abusing Streamlit's primary/secondary semantics.
        if existing and sel_out:
            confirm_key = f"labeling_delete_confirm::{sel_out}"
            st.markdown(
                """
                <style>
                div[class*="st-key-labeling-delete-"] button {
                    background-color: #c62828 !important;
                    color: #ffffff !important;
                    border: 1px solid #b71c1c !important;
                }
                div[class*="st-key-labeling-delete-"] button:hover {
                    background-color: #b71c1c !important;
                    border-color: #8e0000 !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )
            with st.container(key=f"labeling-delete-trigger-{labeling_basename_widget_key(sel_out)}"):
                if st.button(
                    "🗑 Delete reference (PNG + regions + crops)",
                    key="labeling_delete_btn",
                    width="stretch",
                    help=(
                        f"Removes `references/{sel_out}`, every `area.json` screen entry whose "
                        f"`ocr` points at it (with all its regions + version overrides), and the "
                        f"matching files under `references/crop/`. Saves `area.json` immediately."
                    ),
                ):
                    st.session_state[confirm_key] = True

            if st.session_state.get(confirm_key):
                from ui.area_annotator import REPO_ROOT

                impact = preview_delete_reference_impact(
                    REPO_ROOT,
                    ref_root,
                    sel_out,
                    st.session_state.get(AREA_DOC),
                )
                reg_preview = ", ".join(f"`{n}`" for n in impact.region_names[:10])
                if len(impact.region_names) > 10:
                    reg_preview += f" … +{len(impact.region_names) - 10}"
                st.warning(
                    f"Delete `references/{sel_out}` and **everything** linked to it? "
                    "This cannot be undone.\n\n"
                    f"- **{impact.area_entries}** `area.json` screen entr"
                    f"{'y' if impact.area_entries == 1 else 'ies'}\n"
                    f"- **{len(impact.region_names)}** region name(s)"
                    + (f": {reg_preview}" if impact.region_names else "")
                    + f"\n- **{impact.crop_count}** crop tile(s) under `references/crop/`"
                )
                yes_col, no_col = st.columns(2)
                with yes_col, st.container(
                    key=f"labeling-delete-confirm-{labeling_basename_widget_key(sel_out)}"
                ):
                    if st.button(
                        "Yes, delete",
                        key="labeling_delete_yes",
                        icon=":material/delete_forever:",
                        width="stretch",
                    ):
                            n_entries, n_crops, n_err = delete_reference_completely(
                                repo_root=ref_root.parent,
                                ref_root=ref_root,
                                rel_posix=sel_out,
                            )
                            # Reset selection / basename / canvas to the next available reference.
                            st.session_state.pop(LABELING_BN_SYNC_SEL, None)
                            st.session_state.pop(labeling_basename_widget_key(sel_out), None)
                            st.session_state.pop(LABELING_TREE_SELECTION, None)
                            st.session_state[LABELING_REF_TREE_NONCE] = (
                                int(st.session_state.get(LABELING_REF_TREE_NONCE, 0)) + 1
                            )
                            st.session_state[CANVAS_REV] = (
                                int(st.session_state.get(CANVAS_REV, 0)) + 1
                            )
                            st.session_state[CANVAS_LAST_SIG] = ""
                            st.session_state.pop(confirm_key, None)
                            try:
                                if st.query_params.get("ref"):
                                    del st.query_params["ref"]
                            except Exception:
                                pass
                            flash = (
                                f"Deleted `{sel_out}` · dropped {n_entries} `area.json` "
                                f"entry/-ies · removed {n_crops} crop tile(s)"
                            )
                            if n_err:
                                flash += f" · {n_err} error(s)"
                            st.session_state[LABELING_RENAME_FLASH] = flash
                            st.rerun()
                with no_col:
                    if st.button(
                        "Cancel",
                        key="labeling_delete_no",
                        width="stretch",
                    ):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()

        # Lazy import to avoid circular dep with ui.area_annotator.
        from ui.area_annotator import render_active_version_picker

        render_active_version_picker()

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
        target = (ref_root / rel_posix).resolve()
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


def delete_reference_completely(
    repo_root: Path,
    ref_root: Path,
    rel_posix: str,
) -> tuple[int, int, int]:
    """Delete a reference PNG, every ``area.json`` entry pointing at it, and
    every cropped region tile that came from those entries.

    Returns ``(area_entries_dropped, crops_deleted, errors)``. The caller is
    expected to refresh the tree and selection — this function does not
    touch session keys other than ``area_doc``.

    Idempotent: missing files are skipped silently. Saves ``area.json`` to
    disk after mutating session state so the deletion survives a worker /
    Streamlit restart.
    """
    rel_posix = rel_posix.replace("\\", "/").strip()
    if not rel_posix or rel_posix.startswith("..") or "/.." in rel_posix:
        return 0, 0, 0

    try:
        target = (ref_root / rel_posix).resolve()
    except OSError:
        return 0, 0, 1

    doc = st.session_state.get(AREA_DOC)
    if not isinstance(doc, dict):
        purge_reference_png_and_area_entries(repo_root, ref_root, rel_posix)
        return 0, 0, 0

    # Phase 1: collect crop paths from entries that are about to be dropped.
    # Walk both base ``regions[]`` and per-version ``versions[].regions[]`` so
    # ``main_city_v2_*.png`` tiles disappear together with the v2 reference.
    crop_paths: list[Path] = []
    matching_entries = 0
    try:
        from ui.area_annotator import crop_path_for_entry_region
    except Exception:
        crop_path_for_entry_region = None  # type: ignore[assignment]

    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        ocr_raw = str(entry.get("ocr") or "").strip()
        if not ocr_raw:
            continue
        p = Path(ocr_raw)
        if not p.is_absolute():
            p = repo_root / p
        try:
            if p.resolve() != target:
                continue
        except OSError:
            continue
        matching_entries += 1
        if crop_path_for_entry_region is None:
            continue
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            nm = str(reg.get("name") or "").strip()
            if not nm:
                continue
            cp = crop_path_for_entry_region(repo_root, entry, nm)
            if cp is not None and cp.is_file():
                crop_paths.append(cp)
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            vid = str(ver.get("id") or "").strip()
            if not vid:
                continue
            for reg in ver.get("regions") or []:
                if not isinstance(reg, dict):
                    continue
                nm = str(reg.get("name") or "").strip()
                if not nm:
                    continue
                cp = crop_path_for_entry_region(
                    repo_root, entry, nm, active_version=vid
                )
                if cp is not None and cp.is_file():
                    crop_paths.append(cp)

    # Phase 2: drop entries + PNG (in-memory + filesystem for the PNG).
    purge_reference_png_and_area_entries(repo_root, ref_root, rel_posix)

    # Phase 3: delete the crop tiles. Track errors but keep going.
    crops_deleted = 0
    errors = 0
    for cp in crop_paths:
        try:
            cp.unlink(missing_ok=True)
            crops_deleted += 1
        except OSError:
            errors += 1

    # Phase 4: persist area.json so a UI reload (or worker restart) sees the
    # deletion. Mirrors the rename flow, which also writes immediately.
    try:
        from ui.area_annotator import save_json
        from ui.wiki_module import active_wiki_area_path

        save_json(active_wiki_area_path(), doc)
    except Exception:
        errors += 1

    return matching_entries, crops_deleted, errors
