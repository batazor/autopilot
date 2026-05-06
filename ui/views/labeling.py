"""Labeling: reference tree, basename/rename, canvas bound to the selected PNG."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from capture.adb_screencap import adb_screencap_to_file
from config.loader import load_settings
from config.reference_naming import TEMPORAL_SUBDIR, temporal_png_abs_path, unique_label_capture_basename
from ui.area_annotator import (
    REPO_ROOT,
    ensure_entry_for_reference_path,
    export_all_region_crops_for_area_doc,
    render_area_annotator_ui,
)
from ui.keys import (
    AREA_DOC,
    CANVAS_LAST_SIG,
    CANVAS_REV,
    LABELING_BN_SYNC_SEL,
    LABELING_ERROR_FLASH,
    LABELING_LAST_INSTANCE,
    LABELING_PENDING_CAPTURE_REL,
    LABELING_REF_TREE_NONCE,
    LABELING_RENAME_FLASH,
    LABELING_SELECTION_BEFORE_CAPTURE,
    LABELING_TREE_SELECTION,
)
from ui.labeling_reference_panel import (
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
    rel = str(rel).replace("\\", "/").strip()
    # Pending capture may be a temp file under `references/temporal/` (does not touch area.json).
    if rel.startswith(f"{TEMPORAL_SUBDIR}/"):
        try:
            p = (ref_root / rel).resolve()
            if p.is_file():
                p.unlink()
        except OSError:
            pass
    else:
        purge_reference_png_and_area_entries(REPO_ROOT, ref_root, rel)

    existing2 = list_reference_pngs(exclude_temporal=True, exclude_crop=True)
    restored: str | None
    if prev and (ref_root / prev).is_file():
        restored = prev
        st.session_state[LABELING_TREE_SELECTION] = prev
        st.session_state[LABELING_BN_SYNC_SEL] = prev
        st.session_state[labeling_basename_widget_key(prev)] = Path(prev).stem
    elif existing2:
        restored = existing2[0].relative_to(ref_root).as_posix()
        st.session_state[LABELING_TREE_SELECTION] = restored
        st.session_state[LABELING_BN_SYNC_SEL] = restored
        st.session_state[labeling_basename_widget_key(restored)] = Path(restored).stem
    else:
        restored = None
        st.session_state.pop(LABELING_TREE_SELECTION, None)
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
    st.session_state[CANVAS_REV] = int(st.session_state.get(CANVAS_REV, 0)) + 1
    st.session_state[CANVAS_LAST_SIG] = ""
    st.session_state[LABELING_RENAME_FLASH] = f"Removed **references/{rel}** (unsaved capture)."


if LABELING_RENAME_FLASH in st.session_state:
    st.success(st.session_state.pop(LABELING_RENAME_FLASH))

if LABELING_ERROR_FLASH in st.session_state:
    err_txt = str(st.session_state.get(LABELING_ERROR_FLASH) or "").strip()
    if err_txt:
        c_err, c_clear = st.columns([8, 1], vertical_alignment="center")
        with c_err:
            st.error(err_txt)
        with c_clear:
            if st.button("Clear", use_container_width=True, key="labeling_error_clear"):
                st.session_state.pop(LABELING_ERROR_FLASH, None)
                st.rerun()

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

if st.session_state.get(LABELING_LAST_INSTANCE) != instance_id:
    st.session_state[LABELING_LAST_INSTANCE] = instance_id
    st.session_state.pop(LABELING_BN_SYNC_SEL, None)

ref_root = references_root()
existing = list_reference_pngs(exclude_temporal=True, exclude_crop=True)

# Optional deep-link / persistence: `?ref=<path under references/>`
params = st.query_params
ref_param = params.get("ref")
if isinstance(ref_param, str):
    cand = ref_param.replace("\\", "/").strip().lstrip("/")
    if (
        cand
        and not cand.startswith("..")
        and "/.." not in cand
        and (ref_root / cand).is_file()
    ):
        # Two cases:
        # - Regular reference under `references/`: select it in the tree.
        # - Pending capture under `references/temporal/`: restore pending + make it the active ref.
        if cand == TEMPORAL_SUBDIR or cand.startswith(f"{TEMPORAL_SUBDIR}/"):
            st.session_state[LABELING_PENDING_CAPTURE_REL] = cand
            st.session_state[LABELING_TREE_SELECTION] = cand
        else:
            st.session_state[LABELING_TREE_SELECTION] = cand

new_screenshot = False
refresh_screenshot = False
write_crops = False
discard_capture = False

hdr_title, hdr_btn = st.columns([2, 3], vertical_alignment="center")
with hdr_title:
    st.markdown("# Labeling")
with hdr_btn:
    r1c1, r1c2 = st.columns(2, gap="small")
    with r1c1:
        new_screenshot = st.button(
            "New screenshot",
            type="primary",
            use_container_width=True,
            key="labeling_header_capture",
            help=(
                "ADB → new ``references/<instance>_shot_<time>_<rand>.png``; "
                "does not overwrite the selected file."
            ),
        )
    with r1c2:
        refresh_screenshot = st.button(
            "Refresh selected",
            type="secondary",
            use_container_width=True,
            key="labeling_header_refresh",
            help=(
                "ADB → overwrite the **currently selected** PNG under `references/` "
                "(use when the screenshot is stale, but you want to keep the same filename)."
            ),
        )
    pending_rel = st.session_state.get(LABELING_PENDING_CAPTURE_REL)
    can_discard = (
        isinstance(pending_rel, str)
        and pending_rel != ""
        and not pending_rel.startswith("..")
        and (ref_root / pending_rel).is_file()
    )
    r2c1, r2c2 = st.columns(2, gap="small")
    with r2c1:
        discard_capture = st.button(
            "Discard screenshot",
            type="secondary",
            use_container_width=True,
            key="labeling_header_discard",
            disabled=not can_discard,
            help="Delete the last **New screenshot** file and drop its in-memory area.json row "
            "if you have not saved yet. Cleared automatically after **Save area.json**.",
        )
    with r2c2:
        write_crops = st.button(
            "Write crops",
            type="secondary",
            use_container_width=True,
            key="labeling_header_crops",
            help=(
                "Save bbox crops under **references/crop/** for **every** ``area.json`` screen "
                "whose ``ocr`` PNG exists (skips ``overlay_auxiliary`` regions and missing files)."
            ),
        )

if discard_capture:
    _handle_discard_pending_capture(ref_root=ref_root)
    st.rerun()

# Handle capture early so the UI reacts immediately on click,
# without waiting for the (potentially heavy) annotator/canvas to render.
if new_screenshot:
    capture_bn = unique_label_capture_basename(instance_id)
    with st.spinner("Capturing screenshot via ADB…"):
        # Capture to temporal first; move to `references/` only when user assigns a basename.
        temp_path = temporal_png_abs_path(REPO_ROOT, capture_bn)
        ok, msg = adb_screencap_to_file(
            temp_path,
            adb_bin=get_ui_adb_bin(),
            serial=inst_cfg.bluestacks_window_title,
        )
        fname = temp_path.relative_to(ref_root).as_posix()
    if not ok:
        st.session_state[LABELING_ERROR_FLASH] = (
            msg if isinstance(msg, str) and msg.strip() else "ADB capture failed."
        )
    else:
        old_pending = st.session_state.get(LABELING_PENDING_CAPTURE_REL)
        if (
            isinstance(old_pending, str)
            and old_pending
            and old_pending != fname
            and not old_pending.startswith("..")
        ):
            if old_pending.startswith(f"{TEMPORAL_SUBDIR}/"):
                try:
                    p = (ref_root / old_pending.replace("\\", "/")).resolve()
                    if p.is_file():
                        p.unlink()
                except OSError:
                    pass
            else:
                purge_reference_png_and_area_entries(REPO_ROOT, ref_root, old_pending)

        raw_prev = st.session_state.get(LABELING_TREE_SELECTION)
        prev_ok = (
            raw_prev
            if isinstance(raw_prev, str) and (ref_root / raw_prev).is_file()
            else None
        )
        st.session_state[LABELING_SELECTION_BEFORE_CAPTURE] = prev_ok
        st.session_state[LABELING_PENDING_CAPTURE_REL] = fname
        st.session_state[LABELING_TREE_SELECTION] = fname
        try:
            st.query_params["ref"] = fname
        except Exception:
            pass
        st.session_state["_labeling_last_ref_param"] = fname
        st.session_state[LABELING_REF_TREE_NONCE] = (
            int(st.session_state.get(LABELING_REF_TREE_NONCE, 0)) + 1
        )
        st.session_state[LABELING_RENAME_FLASH] = f"Captured temp **references/{fname}**"
        st.rerun()

render_area_annotator_ui(
    labeling_mode=True,
    labeling_ref_root=ref_root,
    labeling_existing=existing,
    labeling_instance_id=instance_id,
)

sel = labeling_resolve_sel(ref_root, existing)
if sel:
    # Keep URL stable across reloads/back/forward.
    last_ref = st.session_state.get("_labeling_last_ref_param")
    # When capturing a new screenshot we intentionally want `?ref=` to point to the
    # pending temporal file; avoid overwriting it with the current tree selection.
    if not new_screenshot and last_ref != sel:
        st.session_state["_labeling_last_ref_param"] = sel
        try:
            st.query_params["ref"] = sel
        except Exception:
            pass

if write_crops:
    doc = st.session_state.get(AREA_DOC)
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

if refresh_screenshot:
    if not sel:
        st.warning("Nothing selected to refresh.")
        st.stop()
    target_rel = str(sel).replace("\\", "/").strip()
    if not target_rel or target_rel.startswith("..") or "/.." in target_rel:
        st.error("Invalid selected path.")
        st.stop()
    target = (ref_root / target_rel).resolve()
    if not target.is_file():
        st.error(f"Selected file missing: `{target_rel}`")
        st.stop()
    with st.spinner(f"Refreshing selected screenshot via ADB → `{target_rel}` …"):
        ok, msg = adb_screencap_to_file(
            target,
            adb_bin=get_ui_adb_bin(),
            serial=inst_cfg.bluestacks_window_title,
        )
    if not ok:
        with st.expander("ADB error details", expanded=True):
            st.error(msg)
    else:
        st.session_state[LABELING_RENAME_FLASH] = f"Refreshed **references/{target_rel}**"
    st.rerun()
