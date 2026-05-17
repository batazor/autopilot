"""Labeling: reference tree, basename/rename, canvas bound to the selected PNG."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import streamlit as st

from config.loader import load_settings
from config.reference_naming import (
    TEMPORAL_SUBDIR,
    temporal_png_abs_path_in_refs,
    unique_label_capture_basename,
)
from layout.area_versions import normalize_version_id
from ui.area_annotator import (
    REPO_ROOT,
    apply_active_version_from_labeling_query,
    detect_screen_id_from_png_path,
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
    LABELING_AREA_DIRTY,
    LABELING_BN_SYNC_SEL,
    LABELING_CAPTURE_SCREEN_ID_REL,
    LABELING_CAPTURE_SCREEN_ID_VALUE,
    LABELING_ERROR_FLASH,
    LABELING_LAST_INSTANCE,
    LABELING_PENDING_CAPTURE_REL,
    LABELING_REF_TREE_NONCE,
    LABELING_REFRESH_PENDING,
    LABELING_RENAME_FLASH,
    LABELING_SELECTION_BEFORE_CAPTURE,
    LABELING_TEMPORAL_REGIONS,
    LABELING_TREE_SELECTION,
)
from ui.labeling_helpers import _count_regions, labeling_workflow_steps
from ui.labeling_reference_panel import (
    labeling_basename_widget_key,
    labeling_query_ref,
    labeling_resolve_sel,
    purge_reference_png_and_area_entries,
)
from ui.labeling_refresh_target import ocr_path_to_ref_rel, resolve_labeling_refresh_target_rel
from ui.labeling_version_redirect import resolve_version_ref_redirect
from ui.labeling_workflow_ui import render_labeling_workflow_strip
from ui.reference_preview import copy_rolling_preview_to, list_reference_pngs
from ui.roboflow_upload import build_coco_annotation, load_roboflow_upload_config, upload_screenshot_to_roboflow
from ui.settings_state import ensure_ui_settings_session_defaults
from ui.wiki_module import render_wiki_module_selector


def _labeling_has_version_query_param(params: Any) -> bool:
    """``params`` is ``st.query_params`` or a dict — both expose ``__contains__``."""
    try:
        return "version" in params
    except Exception:
        return False


def _labeling_query_version_raw(params: Any) -> str:
    """``params`` is ``st.query_params`` or a dict — both expose ``.get``."""
    try:
        raw = params.get("version")
    except Exception:
        return ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    if raw is None:
        return ""
    return str(raw)


def _safe_ref_query_value(raw: object) -> str:
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    return str(raw or "").replace("\\", "/").strip().lstrip("/")


def _resolve_labeling_ref_query(
    raw: object,
) -> tuple[Path, str, str] | None:
    """Resolve ``?ref=`` to ``(reference_root, repo_prefix, rel_under_root)``.

    Canonical URL format is repo-relative and always includes a ``references``
    directory, for example ``references/foo.png`` or
    ``modules/vip/references/foo.png``.
    """

    cand = _safe_ref_query_value(raw)
    if not cand or cand.startswith("..") or "/.." in cand:
        return None

    repo_candidate = (REPO_ROOT / cand).resolve()
    try:
        repo_rel = repo_candidate.relative_to(REPO_ROOT.resolve())
    except ValueError:
        return None
    if not repo_candidate.is_file() or repo_candidate.suffix.lower() != ".png":
        return None

    parts = repo_rel.parts
    for idx in range(len(parts) - 1, -1, -1):
        if parts[idx] != "references":
            continue
        rel_parts = parts[idx + 1 :]
        if not rel_parts:
            continue
        ref_root = (REPO_ROOT / Path(*parts[: idx + 1])).resolve()
        ref_prefix = Path(*parts[: idx + 1]).as_posix()
        rel_under_root = Path(*rel_parts).as_posix()
        return ref_root, ref_prefix, rel_under_root
    return None


def _labeling_ref_query_value(
    rel_under_root: str,
    *,
    ref_prefix: str,
) -> str:
    ref_root = (REPO_ROOT / str(ref_prefix or "").replace("\\", "/").strip().strip("/")).resolve()
    return labeling_query_ref(rel_under_root, ref_root)


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
    st.session_state.pop(LABELING_CAPTURE_SCREEN_ID_REL, None)
    st.session_state.pop(LABELING_CAPTURE_SCREEN_ID_VALUE, None)
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
        st.session_state.entry_idx = ensure_entry_for_reference_path(
            entries,
            ocr_norm,
            references_prefix=ref_root.relative_to(REPO_ROOT).as_posix(),
        )
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

wiki_ctx = render_wiki_module_selector(
    help="Scope screenshots and area layout to Core or a feature module.",
)

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
if st.session_state.get(LABELING_LAST_INSTANCE) != instance_id:
    st.session_state[LABELING_LAST_INSTANCE] = instance_id
    st.session_state.pop(LABELING_BN_SYNC_SEL, None)

ref_root = wiki_ctx.references_dir
ref_prefix = wiki_ctx.references_prefix
_ref_query_resolution = _resolve_labeling_ref_query(st.query_params.get("ref"))
if _ref_query_resolution is not None:
    ref_root, ref_prefix, _ref_query_rel = _ref_query_resolution
    st.session_state[LABELING_TREE_SELECTION] = _ref_query_rel
existing = list_reference_pngs(
    exclude_temporal=True,
    exclude_crop=True,
    root=ref_root,
)

init_session()
st.session_state.setdefault(LABELING_AREA_DIRTY, False)

# Old deep-links pointed at version-specific PNGs (`?ref=main_city_v2.png`); v3 schema
# canonical form is base ref + `?version=v2`. Redirect once before reading params so the
# rest of the page sees the canonical form.
_redir = resolve_version_ref_redirect(
    st.session_state.area_doc,
    st.query_params.get("ref"),
)
if _redir is not None:
    st.query_params["ref"] = _redir[0]
    st.query_params["version"] = _redir[1]
    st.rerun()

# Optional deep-link / persistence:
# `?ref=<repo-relative .../references/file.png>` and `?version=v2|default`
params = st.query_params
ref_param = params.get("ref")
if ref_param is not None:
    resolved_ref = _resolve_labeling_ref_query(ref_param)
    if resolved_ref is not None:
        ref_root, ref_prefix, cand = resolved_ref
        # Two cases:
        # - Regular reference under the active references root: select it in the tree.
        # - Pending capture under `temporal/`: restore pending + make it the active ref.
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
            ocr_norm = f"{ref_prefix}/{rs}".replace("\\", "/")
            ei0 = ensure_entry_for_reference_path(
                entries0,
                ocr_norm,
                references_prefix=ref_prefix,
            )
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
upload_roboflow = False

hdr_title, hdr_btn = st.columns([2, 3], vertical_alignment="center")
with hdr_title:
    st.markdown("# Labeling")
    st.caption(
        f"Module **{wiki_ctx.title}** · references `{ref_root.relative_to(REPO_ROOT)}` · "
        f"area `{wiki_ctx.area_path.relative_to(REPO_ROOT)}`"
    )
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
    raw_upload_sel = st.session_state.get(LABELING_TREE_SELECTION)
    upload_rel = raw_upload_sel.replace("\\", "/").strip() if isinstance(raw_upload_sel, str) else ""
    if not upload_rel and existing:
        upload_rel = existing[0].relative_to(ref_root).as_posix()
    can_upload_roboflow = bool(
        upload_rel
        and upload_rel != TEMPORAL_SUBDIR
        and not upload_rel.startswith("..")
        and "/.." not in upload_rel
        and (ref_root / upload_rel).is_file()
    )
    upload_roboflow = st.button(
        "Upload to Roboflow",
        type="secondary",
        width="stretch",
        key="labeling_header_roboflow_upload",
        disabled=not can_upload_roboflow,
        help=(
            "Upload the selected screenshot to Roboflow. Configure "
            "`ROBOFLOW_API_KEY`, `ROBOFLOW_WORKSPACE`, `ROBOFLOW_PROJECT` "
            "and optional `ROBOFLOW_BATCH_NAME` in `.env`."
        ),
    )

_sel_for_flow = st.session_state.get(LABELING_TREE_SELECTION)
_pending_for_flow = st.session_state.get(LABELING_PENDING_CAPTURE_REL)
_entry_for_flow: dict | None = None
_region_n = 0
_ei_flow = int(st.session_state.get("entry_idx", -1))
_doc_flow = st.session_state.get("area_doc")
if isinstance(_doc_flow, dict):
    _screens_flow = _doc_flow.get("screens")
    if isinstance(_screens_flow, list) and 0 <= _ei_flow < len(_screens_flow):
        _cand = _screens_flow[_ei_flow]
        if isinstance(_cand, dict):
            _entry_for_flow = _cand
            _region_n = _count_regions(_cand)
if isinstance(_sel_for_flow, str) and _sel_for_flow.startswith(f"{TEMPORAL_SUBDIR}/"):
    _tregs = st.session_state.get(LABELING_TEMPORAL_REGIONS)
    if isinstance(_tregs, list):
        _region_n = len([r for r in _tregs if isinstance(r, dict) and str(r.get("name") or "").strip()])

render_labeling_workflow_strip(
    labeling_workflow_steps(
        pending_rel=_pending_for_flow if isinstance(_pending_for_flow, str) else None,
        sel_rel=_sel_for_flow if isinstance(_sel_for_flow, str) else None,
        entry=_entry_for_flow,
        region_count=_region_n,
        area_saved=not bool(st.session_state.get(LABELING_AREA_DIRTY)),
    )
)

# Placeholder: refresh confirmation is rendered here visually but filled after the annotator
# so ``sel`` / ``entry_idx`` match the tree (same logic as before).
labeling_refresh_confirm_slot = st.empty()

if discard_capture:
    _handle_discard_pending_capture(ref_root=ref_root)
    st.rerun()

# Handle capture early so the UI reacts immediately on click,
# without waiting for the (potentially heavy) annotator/canvas to render.
if new_screenshot:
    capture_bn = unique_label_capture_basename(instance_id)
    with st.spinner("Copying latest rolling frame…"):
        # Snapshot the worker's rolling PNG into temporal/ — the rolling loop is
        # the only ADB capture path in the system, so we just copy its output.
        # Move to `references/` only when user assigns a basename.
        temp_path = temporal_png_abs_path_in_refs(ref_root, capture_bn)
        ok, msg = copy_rolling_preview_to(instance_id, temp_path)
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
            st.query_params["ref"] = _labeling_ref_query_value(
                fname,
                ref_prefix=ref_prefix,
            )
            if _labeling_has_version_query_param(st.query_params):
                del st.query_params["version"]
        except Exception:
            pass
        st.session_state["_labeling_last_ref_param"] = fname
        st.session_state[LABELING_REF_TREE_NONCE] = (
            int(st.session_state.get(LABELING_REF_TREE_NONCE, 0)) + 1
        )
        flash = f"Captured temp **references/{fname}**"
        with st.spinner("Detecting screen node…"):
            detected = detect_screen_id_from_png_path(temp_path)
        st.session_state[LABELING_CAPTURE_SCREEN_ID_REL] = fname
        st.session_state[LABELING_CAPTURE_SCREEN_ID_VALUE] = detected
        if detected:
            flash += f" · node **`{detected}`** (saved on basename → references/)"
        st.session_state[LABELING_RENAME_FLASH] = flash
        st.rerun()

render_area_annotator_ui(
    labeling_mode=True,
    labeling_ref_root=ref_root,
    labeling_existing=existing,
    labeling_instance_id=instance_id,
    labeling_references_prefix=ref_prefix,
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
            st.query_params["ref"] = _labeling_ref_query_value(
                sel,
                ref_prefix=ref_prefix,
            )
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

if upload_roboflow:
    if not sel:
        st.error("Nothing selected to upload.")
    else:
        rel_upload = str(sel).replace("\\", "/").strip()
        if not rel_upload or rel_upload.startswith("..") or "/.." in rel_upload:
            st.error("Invalid selected path.")
        else:
            upload_path = (ref_root / rel_upload).resolve()
            ref_abs = ref_root.resolve()
            try:
                upload_path.relative_to(ref_abs)
            except ValueError:
                st.error("Invalid selected path (outside references/).")
            else:
                config, missing = load_roboflow_upload_config()
                if config is None:
                    st.error("Roboflow is not configured: missing " + ", ".join(f"`{name}`" for name in missing))
                else:
                    ei_upload = int(st.session_state.get("entry_idx", -1))
                    entries_upload = st.session_state.area_doc.get("screens") or []
                    entry_upload = (
                        entries_upload[ei_upload]
                        if isinstance(entries_upload, list)
                        and 0 <= ei_upload < len(entries_upload)
                        and isinstance(entries_upload[ei_upload], dict)
                        else None
                    )
                    if entry_upload is None:
                        st.error("No area.json entry selected for this screenshot.")
                    else:
                        try:
                            annotation = build_coco_annotation(
                                image_path=upload_path,
                                image_rel=rel_upload,
                                entry=entry_upload,
                                active_version=get_active_version(entry_upload),
                            )
                        except Exception as exc:
                            st.error(f"Could not build COCO annotation: {exc}")
                        else:
                            with st.spinner(f"Uploading `references/{rel_upload}` + annotations to Roboflow…"):
                                try:
                                    upload_screenshot_to_roboflow(upload_path, config, annotation=annotation)
                                except Exception as exc:
                                    st.error(f"Roboflow upload failed: {exc}")
                                else:
                                    st.success(
                                        f"Uploaded `references/{rel_upload}` with "
                                        f"{len(annotation['annotations'])} annotation(s) "
                                        f"to Roboflow batch `{config.batch_name}`."
                                    )

with labeling_refresh_confirm_slot.container():
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
                target_rel_go, _ = _refresh_rel_and_note_for_session(rel_disp)
                if not target_rel_go or target_rel_go.startswith("..") or "/.." in target_rel_go:
                    st.error("Invalid selected path.")
                    st.session_state.pop(LABELING_REFRESH_PENDING, None)
                    st.stop()
                target = (ref_root / target_rel_go).resolve()
                ref_abs = ref_root.resolve()
                try:
                    target.relative_to(ref_abs)
                except ValueError:
                    st.error("Invalid selected path (outside references/).")
                    st.session_state.pop(LABELING_REFRESH_PENDING, None)
                    st.stop()
                with st.spinner(f"Copying rolling frame → `{target_rel_go}` …"):
                    ok, msg = copy_rolling_preview_to(instance_id, target)
                st.session_state.pop(LABELING_REFRESH_PENDING, None)
                if not ok:
                    with st.expander("Rolling preview error", expanded=True):
                        st.error(msg)
                else:
                    st.session_state[LABELING_RENAME_FLASH] = (
                        f"Refreshed **references/{target_rel_go}**"
                    )
                st.rerun()

if write_crops:
    doc = st.session_state.get(AREA_DOC)
    if doc is None:
        st.error("No area document loaded.")
    else:
        with st.status("Writing region crops…", expanded=True) as status:
            prog = st.progress(0)

            def _emit_progress(x: float) -> None:
                prog.progress(x)

            try:
                written, warns = export_all_region_crops_for_area_doc(
                    doc,
                    repo_root=REPO_ROOT,
                    progress=_emit_progress,
                )
            except (OSError, ValueError) as e:
                status.update(label=f"Crop export failed: {e}", state="error")
                written, warns = [], []

            rels = [p.relative_to(REPO_ROOT).as_posix() for p in written]
            if rels:
                preview = "\n".join(f"- `{p}`" for p in rels[:80])
                more = f"\n… and **{len(rels) - 80}** more." if len(rels) > 80 else ""
                st.success(f"Wrote **{len(rels)}** crop(s):\n{preview}{more}")
                status.update(
                    label=f"Wrote {len(rels)} crop(s) → references/crop/",
                    state="complete",
                    expanded=False,
                )
            elif not warns:
                # No writes and no warnings means the export ran but the
                # area document had no usable bbox-bearing regions.
                st.warning(
                    "No crops written — check reference PNG paths and non-auxiliary regions."
                )
                status.update(label="No crops written", state="error")
            if warns:
                with st.expander("Warnings", expanded=False):
                    st.markdown("\n".join(f"- {w}" for w in warns))
