"""Labeling: reference tree, basename/rename, canvas bound to the selected PNG."""

from __future__ import annotations

import contextlib
from pathlib import Path

import streamlit as st

from capture.adb_screencap import adb_screencap_to_file
from config.loader import load_settings
from config.reference_naming import (
    TEMPORAL_SUBDIR,
    temporal_png_abs_path,
    unique_label_capture_basename,
)
from layout.area_versions import normalize_version_id
from ui.area_annotator import (
    REPO_ROOT,
    apply_active_version_from_labeling_query,
    ensure_entry_for_reference_path,
    export_all_region_crops_for_area_doc,
    get_active_version,
    get_active_version_ocr_override,
    init_session,
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
    LABELING_REFRESH_PENDING,
    LABELING_RENAME_FLASH,
    LABELING_SELECTION_BEFORE_CAPTURE,
    LABELING_TREE_SELECTION,
)
from ui.labeling_reference_panel import (
    labeling_basename_widget_key,
    labeling_resolve_sel,
    purge_reference_png_and_area_entries,
)
from ui.labeling_refresh_target import ocr_path_to_ref_rel, resolve_labeling_refresh_target_rel
from ui.reference_preview import list_reference_pngs, references_root
from ui.settings_state import ensure_ui_settings_session_defaults, get_ui_adb_bin


def _labeling_has_version_query_param(params: object) -> bool:
    try:
        return "version" in params  # type: ignore[operator]
    except Exception:
        return False


def _labeling_query_version_raw(params: object) -> str:
    raw = params.get("version")  # type: ignore[union-attr]
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    if raw is None:
        return ""
    return str(raw)


def _refresh_rel_and_note_for_session(rel_disp: str) -> tuple[str, str | None]:
    doc = st.session_state.get(AREA_DOC)
    entry_idx = int(st.session_state.get("entry_idx", -1))
    entries = doc.get("screens") if isinstance(doc, dict) else None
    base_rel: str | None = None
    ver_rel: str | None = None
    if isinstance(entries, list) and 0 <= entry_idx < len(entries):
        entry = entries[entry_idx]
        if isinstance(entry, dict):
            base_rel = ocr_path_to_ref_rel(str(entry.get("ocr") or ""))
            ov = get_active_version_ocr_override(entry)
            ver_rel = ocr_path_to_ref_rel(ov) if ov else None
    return resolve_labeling_refresh_target_rel(
        rel_disp,
        entry_default_ref_rel=base_rel,
        active_version_ref_rel=ver_rel,
        temporal_subdir=TEMPORAL_SUBDIR,
    )


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
            if st.button("Clear", width="stretch", key="labeling_error_clear"):
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

init_session()

# Optional deep-link / persistence: `?ref=<path under references/>` and `?version=v2|default`
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

if _labeling_has_version_query_param(params):
    raw_sel = st.session_state.get(LABELING_TREE_SELECTION)
    doc0 = st.session_state.area_doc
    entries0 = doc0.get("screens") if isinstance(doc0, dict) else None
    if (
        isinstance(entries0, list)
        and isinstance(raw_sel, str)
        and raw_sel.strip()
        and not raw_sel.startswith("..")
        and "/.." not in raw_sel
    ):
        rs = raw_sel.replace("\\", "/").strip()
        temporal_prefix = f"{TEMPORAL_SUBDIR}/"
        if (
            rs != TEMPORAL_SUBDIR
            and not rs.startswith(temporal_prefix)
            and (ref_root / rs).is_file()
        ):
            ocr_norm = (Path("references") / rs).as_posix()
            ei0 = ensure_entry_for_reference_path(entries0, ocr_norm)
            if 0 <= ei0 < len(entries0):
                ent0 = entries0[ei0]
                if isinstance(ent0, dict):
                    apply_active_version_from_labeling_query(
                        ent0,
                        _labeling_query_version_raw(params),
                    )
                    st.session_state.entry_idx = ei0

new_screenshot = False
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
            width="stretch",
            key="labeling_header_capture",
            help=(
                "ADB → new ``references/<instance>_shot_<time>_<rand>.png``; "
                "does not overwrite the selected file."
            ),
        )
    with r1c2:
        if st.button(
            "Refresh selected",
            type="secondary",
            width="stretch",
            key="labeling_header_refresh",
            help=(
                "ADB → overwrite the PNG you are editing under `references/` "
                "(respects **Active editing version** when it uses its own reference image). "
                "Asks for confirmation first."
            ),
        ):
            st.session_state[LABELING_REFRESH_PENDING] = True
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
            width="stretch",
            key="labeling_header_discard",
            disabled=not can_discard,
            help="Delete the last **New screenshot** file and drop its in-memory area.json row "
            "if you have not saved yet. Cleared automatically after **Save area.json**.",
        )
    with r2c2:
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

if st.session_state.get(LABELING_REFRESH_PENDING):
    st.info(
        "**Refresh selected** — confirm overwrite below the labeling workspace "
        "(after the reference tree)."
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
            if _labeling_has_version_query_param(st.query_params):
                del st.query_params["version"]
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
        with contextlib.suppress(Exception):
            st.query_params["ref"] = sel
    sel_norm = str(sel).replace("\\", "/").strip()
    temporal_sel = sel_norm == TEMPORAL_SUBDIR or sel_norm.startswith(f"{TEMPORAL_SUBDIR}/")
    if not new_screenshot and not temporal_sel:
        ei = int(st.session_state.get("entry_idx", -1))
        entries = st.session_state.area_doc.get("screens") or []
        ver_qp = ""
        if isinstance(entries, list) and 0 <= ei < len(entries):
            av = get_active_version(entries[ei])
            ver_qp = normalize_version_id(av) if av else ""
        with contextlib.suppress(Exception):
            if ver_qp:
                st.query_params["version"] = ver_qp
            elif _labeling_has_version_query_param(st.query_params):
                del st.query_params["version"]

if st.session_state.get(LABELING_REFRESH_PENDING):
    if not sel:
        st.warning("Nothing selected to refresh.")
        st.session_state.pop(LABELING_REFRESH_PENDING, None)
    else:
        rel_disp = str(sel).replace("\\", "/").strip()
        target_rel, ver_note = _refresh_rel_and_note_for_session(rel_disp)
        lines = [
            f"This will **write** a new ADB screenshot to `references/{target_rel}` "
            "(same logical target as the canvas; `area.json` regions are unchanged)."
        ]
        if ver_note:
            lines.append(ver_note)
        st.warning("\n\n".join(lines))
        bc1, bc2 = st.columns(2)
        with bc1:
            confirm_refresh = st.button(
                "Confirm overwrite",
                type="primary",
                key="labeling_refresh_confirm_yes",
            )
        with bc2:
            if st.button("Cancel", key="labeling_refresh_confirm_no"):
                st.session_state.pop(LABELING_REFRESH_PENDING, None)
                st.rerun()
        if confirm_refresh:
            target_rel, _ = _refresh_rel_and_note_for_session(rel_disp)
            if not target_rel or target_rel.startswith("..") or "/.." in target_rel:
                st.error("Invalid selected path.")
                st.session_state.pop(LABELING_REFRESH_PENDING, None)
                st.stop()
            target = (ref_root / target_rel).resolve()
            ref_abs = ref_root.resolve()
            try:
                target.relative_to(ref_abs)
            except ValueError:
                st.error("Invalid selected path (outside references/).")
                st.session_state.pop(LABELING_REFRESH_PENDING, None)
                st.stop()
            with st.spinner(f"Refreshing screenshot via ADB → `{target_rel}` …"):
                ok, msg = adb_screencap_to_file(
                    target,
                    adb_bin=get_ui_adb_bin(),
                    serial=inst_cfg.bluestacks_window_title,
                )
            st.session_state.pop(LABELING_REFRESH_PENDING, None)
            if not ok:
                with st.expander("ADB error details", expanded=True):
                    st.error(msg)
            else:
                st.session_state[LABELING_RENAME_FLASH] = (
                    f"Refreshed **references/{target_rel}**"
                )
            st.rerun()

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
                st.warning(
                    "No crops written — check reference PNG paths and non-auxiliary regions."
                )
            if warns:
                with st.expander("Warnings", expanded=False):
                    st.markdown("\n".join(f"- {w}" for w in warns))
        except (OSError, ValueError) as e:
            st.error(str(e))
        finally:
            prog.empty()
