"""Labeling API — reference list, image bytes, area.json regions, capture."""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, cast

from api.services import labeling_scope as ls
from config.module_registry import CORE_MODULE_KEY, normalize_module_scope
from config.paths import repo_root
from config.reference_naming import (
    TEMPORAL_SUBDIR,
    reference_file_basename,
    temporal_png_abs_path_in_refs,
    unique_label_capture_basename,
)
from dashboard.area_doc import (
    _doc_with_repo_relative_ocr,
    _sync_default_regions_into_version,
    detect_screen_id_from_png_path,
    ensure_entry_for_reference_path,
    export_all_region_crops_for_area_doc,
    find_stale_crops,
)
from dashboard.labeling_helpers import build_reference_leaf_meta_index, format_reference_leaf_title
from dashboard.overlay_yaml_sync import (
    apply_region_rename,
    cascade_primary_rename_in_regions,
    detect_region_renames,
)
from dashboard.reference_area_sync import sync_area_json_ocr_after_reference_rename
from dashboard.reference_ocr_paths import reference_basename_stem
from dashboard.reference_preview import (
    capture_preview_to,
    list_reference_pngs,
    move_temporal_to_reference_basename,
    rename_reference_to_basename,
)
from layout.area_versions import (
    VERSION_ID_RE,
    compile_cond,
    get_version_block,
    next_version_id,
    normalize_version_id,
)

logger = logging.getLogger(__name__)

_REPO = repo_root()
_ROLLING_STEM_SUFFIX = "_current_state"


def list_labeling_scopes() -> list[dict[str, Any]]:
    return ls.list_labeling_scopes()


def list_screen_id_options(*, scope: str, current_screen_id: str = "") -> list[str]:
    """Node ids for Screen entry (area.json + navigation graph), same as Streamlit UI."""
    from dashboard.area_doc import screen_id_select_options

    env = ls.scope_env(scope)
    doc = ls.load_area_doc(env)
    return screen_id_select_options(doc, current_screen_id)


def _is_rolling_preview_png(path: Path, ref_root: Path) -> bool:
    """Exclude worker rolling frames ``temporal/{instance}_current_state.png``."""
    try:
        rel = path.resolve().relative_to(ref_root.resolve())
    except ValueError:
        return False
    if len(rel.parts) < 2 or rel.parts[0] != TEMPORAL_SUBDIR:
        return False
    return path.stem.endswith(_ROLLING_STEM_SUFFIX)


def _list_labeling_reference_pngs(env: ls.LabelingScopeEnv, *, limit: int) -> list[Path]:
    """Published refs plus pending ``temporal/*_shot_*.png`` (not rolling previews)."""
    published = list_reference_pngs(
        limit=limit,
        root=env.ref_root,
        exclude_temporal=True,
        exclude_crop=True,
        exclude_events=True,
    )
    published = [p for p in published if not _is_rolling_preview_png(p, env.ref_root)]

    pending: list[Path] = []
    temporal_dir = env.ref_root / TEMPORAL_SUBDIR
    if temporal_dir.is_dir():
        for path in temporal_dir.glob("*.png"):
            if _is_rolling_preview_png(path, env.ref_root):
                continue
            pending.append(path)
        pending.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    seen: set[Path] = set()
    merged: list[Path] = []
    for path in (*pending, *published):
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        merged.append(path)
        if len(merged) >= limit:
            break
    return merged


def list_reference_paths(*, scope: str = CORE_MODULE_KEY, limit: int = 300) -> list[dict[str, Any]]:
    env = ls.scope_env(scope)
    paths = _list_labeling_reference_pngs(env, limit=limit)
    area_doc = ls.load_area_doc(env)
    meta_by_rel = build_reference_leaf_meta_index(area_doc, env.ref_root)
    out: list[dict[str, Any]] = []
    for p in paths:
        try:
            rel_repo = p.resolve().relative_to(env.repo_root).as_posix()
            rel_under = p.resolve().relative_to(env.ref_root).as_posix()
        except ValueError:
            continue
        meta = meta_by_rel.get(rel_under)
        title = format_reference_leaf_title(rel_under, meta)
        out.append(
            {
                "rel": rel_repo,
                "name": p.name,
                "rel_under": rel_under,
                "title": title,
                "screen_id": meta.screen_id if meta else "",
                "region_count": meta.region_count if meta else 0,
                "active_version": meta.active_version if meta else None,
                "unassigned": meta.unassigned if meta else True,
            }
        )
    return out


def list_stale_crops(*, scope: str = CORE_MODULE_KEY, limit: int = 100) -> dict[str, Any]:
    env = ls.scope_env(scope)
    doc = ls.load_area_doc(env)
    doc = _doc_with_repo_relative_ocr(doc, env.area_path, env.repo_root)
    stale = find_stale_crops(cast("dict[str, Any]", doc), repo_root=env.repo_root)
    return {"count": len(stale), "stale": stale[:limit], "scope": normalize_module_scope(scope)}


def _require_writable_area_path(env: ls.LabelingScopeEnv) -> Path:
    """Return the area file path or raise if the scope has none (e.g., All)."""
    if env.area_path is None:
        msg = (
            f"labeling write requested for scope {env.ctx.storage_key!r} which has no "
            "writable area file (the All scope is read-only — pick a module scope)"
        )
        raise ValueError(msg)
    return env.area_path


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w",
        dir=path.parent,
        delete=False,
        suffix=".tmp",
        encoding="utf-8",
    ) as f:
        f.write(payload)
        tmp = f.name
    Path(tmp).replace(path)


def _resolve_edit_version(entry: dict[str, Any], version_raw: str | None) -> str | None:
    if not version_raw or str(version_raw).strip().lower() in {"", "default"}:
        return None
    vid = normalize_version_id(str(version_raw))
    if vid is None:
        return None
    declared = {
        str(v.get("id", "") or "").strip()
        for v in entry.get("versions") or []
        if isinstance(v, dict)
    }
    for raw_id in declared:
        if normalize_version_id(raw_id) == vid:
            return raw_id
    return None


def _regions_for_edit(entry: dict[str, Any], version_id: str | None) -> list[dict[str, Any]]:
    if version_id:
        ver = get_version_block(entry, version_id)
        if ver is not None:
            raw = ver.get("regions")
            if isinstance(raw, list):
                return [r for r in raw if isinstance(r, dict)]
            return []
    raw_regs = entry.get("regions")
    if isinstance(raw_regs, list):
        return [r for r in raw_regs if isinstance(r, dict)]
    return []


def _set_regions_for_edit(
    entry: dict[str, Any],
    version_id: str | None,
    regions: list[dict[str, Any]],
) -> None:
    if version_id:
        ver = get_version_block(entry, version_id)
        if ver is None:
            msg = f"version not found: {version_id}"
            raise ValueError(msg)
        ver["regions"] = regions
        return
    entry["regions"] = regions


def _display_ref_for_entry(ref_rel: str, entry: dict[str, Any], version_id: str | None) -> str:
    if version_id:
        ver = get_version_block(entry, version_id)
        if ver is not None:
            ocr = str(ver.get("ocr") or "").replace("\\", "/").strip().lstrip("/")
            if ocr:
                return ocr
    return ref_rel.replace("\\", "/").strip().lstrip("/")


def _resolve_version_ref_redirect(
    area_doc: dict[str, Any],
    ref_rel: str,
) -> tuple[str, str | None]:
    """Map a version-specific ``ocr`` PNG to base ref + version id."""
    cand = ref_rel.replace("\\", "/").strip().lstrip("/")
    screens = area_doc.get("screens") if isinstance(area_doc, dict) else None
    if not isinstance(screens, list):
        return ref_rel, None
    for entry in screens:
        if not isinstance(entry, dict):
            continue
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            ver_ocr = str(ver.get("ocr") or "").replace("\\", "/").strip().lstrip("/")
            if not ver_ocr or ver_ocr != cand:
                continue
            base_ocr = str(entry.get("ocr") or "").replace("\\", "/").strip().lstrip("/")
            vid = str(ver.get("id") or "").strip()
            if base_ocr and vid:
                return base_ocr, vid
    return ref_rel, None


def get_labeling_document(
    ref_rel: str,
    *,
    version: str | None = None,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    area_doc = ls.load_area_doc(env)
    base_ref, redirect_vid = _resolve_version_ref_redirect(area_doc, ref_rel)
    if redirect_vid and not version:
        ref_rel = base_ref
        version = redirect_vid

    abs_png = (env.repo_root / ref_rel).resolve()
    if not abs_png.is_file():
        msg = f"reference not found: {ref_rel}"
        raise FileNotFoundError(msg)

    doc = area_doc
    found = ls.entry_for_ref(doc, ref_rel, env)
    regions: list[dict[str, Any]] = []
    screen_id = ""
    entry_id: int | None = None
    versions_meta: list[dict[str, Any]] = []
    active_version: str | None = None
    entry: dict[str, Any] | None = None
    if found is not None:
        entry_id, entry = found
        screen_id = str(entry.get("screen_id") or "")
        active_version = _resolve_edit_version(entry, version)
        regions = _regions_for_edit(entry, active_version)
        versions_meta.extend(
            {
                "id": str(v.get("id", "") or "").strip(),
                "cond": str(v.get("cond", "") or ""),
                "ocr": str(v.get("ocr", "") or "").strip() or None,
            }
            for v in entry.get("versions") or []
            if isinstance(v, dict)
        )

    display_ref = _display_ref_for_entry(ref_rel, entry or {}, active_version)
    basename_stem = reference_basename_stem(ls.rel_under_ref_root(ref_rel, env))

    return {
        "ref": ref_rel,
        "display_ref": display_ref,
        "screen_id": screen_id,
        "entry_id": entry_id,
        "regions": regions,
        "versions": versions_meta,
        "active_version": active_version,
        "is_pending": ls.is_pending_temporal_ref(ref_rel, env),
        "basename": basename_stem,
        "area_path": (
            str(env.area_path.relative_to(env.repo_root)) if env.area_path is not None else None
        ),
        "references_prefix": env.references_prefix,
        "scope": normalize_module_scope(scope),
        "module_key": env.ctx.storage_key,
        "module_title": env.ctx.title,
        "redirect_version": redirect_vid,
    }


def save_labeling_regions(
    ref_rel: str,
    regions: list[dict[str, Any]],
    *,
    version: str | None = None,
    screen_id: str | None = None,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    abs_png = (env.repo_root / ref_rel).resolve()
    if not abs_png.is_file():
        msg = f"reference not found: {ref_rel}"
        raise FileNotFoundError(msg)

    doc = ls.load_area_doc(env)
    screens = doc.setdefault("screens", [])
    if not isinstance(screens, list):
        screens = []
        doc["screens"] = screens

    found = ls.entry_for_ref(doc, ref_rel, env)
    if found is None:
        sid_clean = (screen_id or "").strip()
        if not regions and not sid_clean and not version:
            return {
                "ok": True,
                "region_count": 0,
                "active_version": None,
                "screen_id": "",
                "region_renames_synced": [],
                "crops_written_count": 0,
                "crop_warnings": [],
                "skipped": "empty-save-for-unknown-ref",
            }
        ocr_rel = (
            ref_rel
            if ref_rel.startswith(env.references_prefix + "/")
            or ref_rel == env.references_prefix
            else ls.repo_ref_for_under(Path(ref_rel).name, env)
        )
        idx = ensure_entry_for_reference_path(
            screens,
            ocr_rel,
            references_prefix=env.references_prefix,
        )
        entry = screens[idx]
    else:
        idx, entry = found
    active_version = _resolve_edit_version(entry, version) if found else None
    if version and active_version is None and found is not None:
        msg = f"unknown version: {version}"
        raise ValueError(msg)

    old_regions = _regions_for_edit(entry, active_version)
    rename_pairs = detect_region_renames(old_regions, regions)
    synced_renames: list[dict[str, Any]] = []
    regions_to_save = list(regions)
    ocr_rel = str(entry.get("ocr") or ref_rel).replace("\\", "/").strip()
    module_dir = env.ctx.module_dir

    for old_name, new_name in rename_pairs:
        if old_name.endswith(("_search", "_tap")):
            continue
        regions_to_save = cascade_primary_rename_in_regions(
            regions_to_save, old_name, new_name
        )
        if str(entry.get("screen_region") or "").strip() == old_name:
            entry["screen_region"] = new_name
        sync = apply_region_rename(
            env.repo_root,
            old_name=old_name,
            new_name=new_name,
            module_dir=module_dir,
            reference_repo_rel=ocr_rel or None,
        )
        synced_renames.append(sync)

    _set_regions_for_edit(entry, active_version, regions_to_save)
    if screen_id is not None:
        entry["screen_id"] = str(screen_id).strip()
    screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    crop_meta = _export_module_crops(doc, env)
    _publish_area_manifest_changed()
    return {
        "ok": True,
        "region_count": len(regions_to_save),
        "active_version": active_version,
        "screen_id": str(entry.get("screen_id") or ""),
        "region_renames_synced": synced_renames,
        **crop_meta,
    }


def _publish_area_manifest_changed() -> None:
    """Notify dashboard SSE subscribers (Region probe, overlay test) after area save."""
    try:
        from api.deps import get_redis
        from dashboard.dashboard_events import publish_dashboard_event

        publish_dashboard_event(get_redis(), topic="area", reason="labeling_save")
    except Exception:
        logger.debug("area manifest dashboard event skipped", exc_info=True)


def _export_module_crops(doc: dict[str, Any], env: ls.LabelingScopeEnv) -> dict[str, Any]:
    """Re-export all bbox crops for the module after area.json was saved."""
    doc_export = _doc_with_repo_relative_ocr(doc, env.area_path, env.repo_root)
    written, warnings = export_all_region_crops_for_area_doc(
        cast("dict[str, Any]", doc_export),
        repo_root=env.repo_root,
    )
    rels = [p.relative_to(env.repo_root).as_posix() for p in written]
    return {
        "crops_written_count": len(rels),
        "crop_warnings": warnings[:50],
    }


def read_reference_bytes(ref_rel: str) -> bytes:
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    if ".." in Path(ref_rel).parts:
        msg = "invalid path"
        raise ValueError(msg)
    path = (_REPO / ref_rel).resolve()
    if not path.is_file() or path.suffix.lower() != ".png":
        msg = f"not a png file: {ref_rel}"
        raise FileNotFoundError(msg)
    return path.read_bytes()


def import_dropped_png(
    content: bytes,
    instance_id: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Save a dropped PNG into ``references/temporal/`` (same flow as New screenshot)."""
    env = ls.scope_env(scope)
    iid = instance_id.strip()
    if not iid:
        msg = "instance_id is required"
        raise ValueError(msg)
    if not content:
        msg = "empty file"
        raise ValueError(msg)
    capture_bn = unique_label_capture_basename(iid)
    temp_path = temporal_png_abs_path_in_refs(env.ref_root, capture_bn)
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path.write_bytes(content)
    ref_rel = temp_path.resolve().relative_to(env.repo_root).as_posix()
    return {
        "ok": True,
        "ref": ref_rel,
        "instance_id": iid,
        "scope": normalize_module_scope(scope),
    }


def capture_new_screenshot(
    instance_id: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Copy the worker rolling preview into ``<prefix>/temporal/<shot>.png``."""
    env = ls.scope_env(scope)
    iid = instance_id.strip()
    if not iid:
        msg = "instance_id is required"
        raise ValueError(msg)
    capture_bn = unique_label_capture_basename(iid)
    temp_path = temporal_png_abs_path_in_refs(env.ref_root, capture_bn)
    ok, msg = capture_preview_to(iid, temp_path)
    if not ok:
        raise RuntimeError(msg or "ADB capture failed")
    ref_rel = temp_path.resolve().relative_to(env.repo_root).as_posix()
    return {"ok": True, "ref": ref_rel, "instance_id": iid, "scope": normalize_module_scope(scope)}


def refresh_reference(
    ref_rel: str,
    instance_id: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Overwrite an existing reference PNG from the rolling preview."""
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    iid = instance_id.strip()
    if not iid:
        msg = "instance_id is required"
        raise ValueError(msg)
    target = (env.repo_root / ref_rel).resolve()
    if not target.is_file():
        msg = f"reference not found: {ref_rel}"
        raise FileNotFoundError(msg)
    if _is_rolling_preview_png(target, env.ref_root):
        msg = "cannot refresh rolling preview files"
        raise ValueError(msg)
    ok, msg = capture_preview_to(iid, target)
    if not ok:
        raise RuntimeError(msg or "ADB capture failed")
    return {"ok": True, "ref": ref_rel, "instance_id": iid}


def discard_pending_capture(
    ref_rel: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Delete an unsaved temporal capture (does not touch area.json)."""
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    if ".." in Path(ref_rel).parts:
        msg = "invalid path"
        raise ValueError(msg)
    temporal_prefix = f"{env.references_prefix}/{TEMPORAL_SUBDIR}/"
    if not ref_rel.startswith(temporal_prefix):
        msg = f"only pending captures under {temporal_prefix} can be discarded"
        raise ValueError(msg)
    path = (env.repo_root / ref_rel).resolve()
    if _is_rolling_preview_png(path, env.ref_root):
        msg = "cannot discard rolling preview files"
        raise ValueError(msg)
    if path.is_file():
        path.unlink()
    return {"ok": True, "ref": ref_rel}


def promote_reference(
    ref_rel: str,
    basename: str,
    instance_id: str,
    *,
    regions: list[dict[str, Any]] | None = None,
    screen_id: str | None = None,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Move a pending temporal capture to ``<prefix>/<basename>.png``."""
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    if not ls.is_pending_temporal_ref(ref_rel, env):
        msg = f"only pending captures under {env.references_prefix}/{TEMPORAL_SUBDIR}/ can be promoted"
        raise ValueError(msg)
    src = (env.repo_root / ref_rel).resolve()
    ok, msg, new_rel = move_temporal_to_reference_basename(
        src_temporal=src,
        name_input=basename,
        instance_id=instance_id,
        references_dir=env.ref_root,
    )
    if not ok or not new_rel:
        raise RuntimeError(msg or "promote failed")

    new_ref_rel = ls.repo_ref_for_under(new_rel, env)
    doc = ls.load_area_doc(env)
    screens = doc.setdefault("screens", [])
    if not isinstance(screens, list):
        screens = []
        doc["screens"] = screens
    ocr_rel = new_ref_rel
    idx = ensure_entry_for_reference_path(
        screens,
        ocr_rel,
        references_prefix=env.references_prefix,
    )
    entry = screens[idx]
    if regions is not None:
        entry["regions"] = regions
    sid = (screen_id or "").strip()
    if not sid:
        detected = detect_screen_id_from_png_path((env.repo_root / new_ref_rel).resolve())
        if detected:
            sid = detected
    if sid:
        entry["screen_id"] = sid
    screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    return {
        "ok": True,
        "ref": new_ref_rel,
        "rel_under_refs": new_rel,
        "screen_id": str(entry.get("screen_id") or ""),
        "message": msg,
    }


def rename_reference(
    ref_rel: str,
    basename: str,
    instance_id: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Rename an on-disk reference PNG and sync area manifest ``ocr`` paths."""
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    if ls.is_pending_temporal_ref(ref_rel, env):
        msg = "use promote for pending temporal captures"
        raise ValueError(msg)
    src = (env.repo_root / ref_rel).resolve()
    if not src.is_file():
        msg = f"reference not found: {ref_rel}"
        raise FileNotFoundError(msg)
    old_under = ls.rel_under_ref_root(ref_rel, env)
    ok, msg = rename_reference_to_basename(
        src,
        basename,
        instance_id,
        references_dir=env.ref_root,
    )
    if not ok:
        raise RuntimeError(msg or "rename failed")
    dest_base = reference_file_basename(basename.strip(), instance_id)
    new_rel = f"{dest_base}.png"
    new_ref_rel = ls.repo_ref_for_under(new_rel, env)
    sync_ok, sync_err, n_ocr = sync_area_json_ocr_after_reference_rename(
        env.repo_root,
        old_rel_under_refs=old_under,
        new_rel_under_refs=new_rel,
        area_path=env.area_path,
        references_prefix=env.references_prefix,
    )
    out: dict[str, Any] = {
        "ok": True,
        "ref": new_ref_rel,
        "rel_under_refs": new_rel,
        "message": msg,
        "ocr_paths_updated": n_ocr,
    }
    if not sync_ok and sync_err:
        renamed_path = (env.ref_root / new_rel).resolve()
        try:
            renamed_path.rename((env.ref_root / old_under).resolve())
        except OSError as rollback_exc:
            msg_rb = (
                f"Renamed to {new_rel} but area.json sync failed: {sync_err} "
                f"(rollback failed: {rollback_exc})"
            )
            raise RuntimeError(msg_rb) from rollback_exc
        msg_sync = f"area.json sync failed: {sync_err}"
        raise RuntimeError(msg_sync) from None
    return out


def add_version(
    ref_rel: str,
    version_id: str,
    cond: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    vid = normalize_version_id(version_id)
    cond_clean = (cond or "").strip()
    if vid is None or not VERSION_ID_RE.match(vid):
        msg = "version id must be vN (e.g. v2, v3)"
        raise ValueError(msg)
    if not cond_clean:
        msg = "cond expression is required"
        raise ValueError(msg)
    try:
        compile_cond(cond_clean)
    except SyntaxError as exc:
        msg = f"cond syntax error: {exc}"
        raise ValueError(msg) from exc

    doc = ls.load_area_doc(env)
    found = ls.entry_for_ref(doc, ref_rel, env)
    if found is None:
        msg = "no area.json entry — save regions or promote the reference first"
        raise ValueError(msg)
    idx, entry = found
    versions = list(entry.get("versions") or [])
    declared = {str(v.get("id", "") or "").strip() for v in versions if isinstance(v, dict)}
    if vid in declared:
        msg = f"version {vid!r} already exists"
        raise ValueError(msg)
    versions.append({"id": vid, "cond": cond_clean})
    entry["versions"] = versions
    screens = doc.setdefault("screens", [])
    if isinstance(screens, list):
        screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    return {"ok": True, "version_id": vid}


def update_version_cond(
    ref_rel: str,
    version_id: str,
    cond: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    cond_clean = (cond or "").strip()
    if not cond_clean:
        msg = "cond cannot be empty"
        raise ValueError(msg)
    try:
        compile_cond(cond_clean)
    except SyntaxError as exc:
        msg = f"cond syntax error: {exc}"
        raise ValueError(msg) from exc

    doc = ls.load_area_doc(env)
    found = ls.entry_for_ref(doc, ref_rel, env)
    if found is None:
        msg = "no area.json entry for this reference"
        raise ValueError(msg)
    idx, entry = found
    vid = _resolve_edit_version(entry, version_id) or normalize_version_id(version_id)
    if not vid:
        msg = f"unknown version: {version_id}"
        raise ValueError(msg)
    ver = get_version_block(entry, vid)
    if ver is None:
        msg = f"unknown version: {version_id}"
        raise ValueError(msg)
    ver["cond"] = cond_clean
    screens = doc.setdefault("screens", [])
    if isinstance(screens, list):
        screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    return {"ok": True, "version_id": vid}


def bind_version_ocr(
    ref_rel: str,
    version_id: str,
    ocr: str | None,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    doc = ls.load_area_doc(env)
    found = ls.entry_for_ref(doc, ref_rel, env)
    if found is None:
        msg = "no area.json entry for this reference"
        raise ValueError(msg)
    idx, entry = found
    vid = _resolve_edit_version(entry, version_id) or normalize_version_id(version_id)
    if not vid:
        msg = f"unknown version: {version_id}"
        raise ValueError(msg)
    ver = get_version_block(entry, vid)
    if ver is None:
        msg = f"unknown version: {version_id}"
        raise ValueError(msg)
    ocr_clean = (ocr or "").replace("\\", "/").strip().lstrip("/")
    if ocr_clean:
        if ".." in Path(ocr_clean).parts:
            msg = "invalid ocr path"
            raise ValueError(msg)
        if not (env.repo_root / ocr_clean).resolve().is_file():
            msg = f"reference not found: {ocr_clean}"
            raise FileNotFoundError(msg)
        ver["ocr"] = ocr_clean
    else:
        ver.pop("ocr", None)
    screens = doc.setdefault("screens", [])
    if isinstance(screens, list):
        screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    return {"ok": True, "version_id": vid, "ocr": ocr_clean or None}


def delete_version(
    ref_rel: str,
    version_id: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    doc = ls.load_area_doc(env)
    found = ls.entry_for_ref(doc, ref_rel, env)
    if found is None:
        msg = "no area.json entry for this reference"
        raise ValueError(msg)
    idx, entry = found
    vid = _resolve_edit_version(entry, version_id) or normalize_version_id(version_id)
    if not vid:
        msg = f"unknown version: {version_id}"
        raise ValueError(msg)
    versions = [v for v in entry.get("versions") or [] if isinstance(v, dict)]
    entry["versions"] = [v for v in versions if str(v.get("id", "") or "").strip() != vid]
    screens = doc.setdefault("screens", [])
    if isinstance(screens, list):
        screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    return {"ok": True, "version_id": vid}


def sync_version_regions_from_default(
    ref_rel: str,
    version_id: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    doc = ls.load_area_doc(env)
    found = ls.entry_for_ref(doc, ref_rel, env)
    if found is None:
        msg = "no area.json entry for this reference"
        raise ValueError(msg)
    idx, entry = found
    vid = _resolve_edit_version(entry, version_id) or normalize_version_id(version_id)
    if not vid:
        msg = f"unknown version: {version_id}"
        raise ValueError(msg)
    added, skipped = _sync_default_regions_into_version(cast("Any", entry), vid)
    screens = doc.setdefault("screens", [])
    if isinstance(screens, list):
        screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    return {"ok": True, "added": added, "skipped": skipped, "version_id": vid}


def suggest_next_version_id(ref_rel: str, *, scope: str = CORE_MODULE_KEY) -> dict[str, str]:
    env = ls.scope_env(scope)
    doc = ls.load_area_doc(env)
    found = ls.entry_for_ref(doc, ref_rel, env)
    declared: list[str] = []
    if found is not None:
        _idx, entry = found
        declared = [
            str(v.get("id", "") or "").strip()
            for v in entry.get("versions") or []
            if isinstance(v, dict)
        ]
    return {"suggested_id": next_version_id(declared)}


def export_region_crops(*, scope: str = CORE_MODULE_KEY) -> dict[str, Any]:
    """Write bbox crops for every screen in the active module's area manifest."""
    env = ls.scope_env(scope)
    doc = ls.load_area_doc(env)
    doc = _doc_with_repo_relative_ocr(doc, env.area_path, env.repo_root)
    written, warnings = export_all_region_crops_for_area_doc(doc, repo_root=env.repo_root)
    rels = [p.relative_to(env.repo_root).as_posix() for p in written]
    return {
        "ok": True,
        "written_count": len(rels),
        "written": rels[:200],
        "warnings": warnings[:100],
        "truncated": len(rels) > 200 or len(warnings) > 100,
    }
