"""Labeling API — reference list, image bytes, area.json regions, capture."""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any, cast

from api.services import labeling_scope as ls
from config.games import default_game
from config.module_registry import (
    CORE_MODULE_KEY,
    list_labeling_modules,
    normalize_module_scope,
)
from config.paths import repo_root
from config.reference_naming import (
    TEMPORAL_SUBDIR,
    reference_file_basename,
    temporal_png_abs_path_in_refs,
    unique_label_capture_basename,
)
from dashboard.area_doc import (
    _doc_with_repo_relative_ocr,
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


def _list_reference_pngs_for_root(ref_root: Path, *, limit: int) -> list[Path]:
    """Published refs plus pending ``temporal/*_shot_*.png`` (not rolling previews)."""
    published = list_reference_pngs(
        limit=limit,
        root=ref_root,
        exclude_temporal=True,
        exclude_crop=True,
        exclude_events=True,
        exclude_maps=True,
    )
    published = [p for p in published if not _is_rolling_preview_png(p, ref_root)]

    pending: list[Path] = []
    temporal_dir = ref_root / TEMPORAL_SUBDIR
    if temporal_dir.is_dir():
        for path in temporal_dir.glob("*.png"):
            if _is_rolling_preview_png(path, ref_root):
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


def _reference_roots_for_scope(env: ls.LabelingScopeEnv) -> list[Path]:
    if not env.ctx.is_all:
        return [env.ref_root]

    roots: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        root = path.resolve()
        if root not in seen:
            seen.add(root)
            roots.append(root)

    game = env.ctx.game or default_game()
    for ctx in list_labeling_modules(env.repo_root, game=game):
        add(ctx.references_dir)
    if game == default_game():
        add(env.repo_root / "references")
    return roots


def _list_labeling_reference_pngs(env: ls.LabelingScopeEnv, *, limit: int) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for root in _reference_roots_for_scope(env):
        for path in _list_reference_pngs_for_root(root, limit=limit):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(path)
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[:limit]


def list_reference_paths(*, scope: str = CORE_MODULE_KEY, limit: int = 300) -> list[dict[str, Any]]:
    env = ls.scope_env(scope)
    paths = _list_labeling_reference_pngs(env, limit=limit)
    area_doc = ls.load_area_doc(env)
    meta_root = env.repo_root if env.ctx.is_all else env.ref_root
    meta_by_rel = build_reference_leaf_meta_index(area_doc, meta_root)
    out: list[dict[str, Any]] = []
    for p in paths:
        try:
            rel_repo = p.resolve().relative_to(env.repo_root).as_posix()
            rel_under = (
                rel_repo
                if env.ctx.is_all
                else p.resolve().relative_to(env.ref_root).as_posix()
            )
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


def _write_env_for_reference(
    env: ls.LabelingScopeEnv,
    ref_rel: str,
) -> ls.LabelingScopeEnv:
    """Resolve the writable module env for a reference selected in ``scope=all``."""
    if not env.ctx.is_all:
        return env

    ref_norm = ref_rel.replace("\\", "/").strip().lstrip("/")
    game = env.ctx.game or default_game()
    matches: list[ls.LabelingScopeEnv] = []
    for ctx in list_labeling_modules(env.repo_root, game=game):
        prefix = ctx.references_prefix.rstrip("/") + "/"
        if ref_norm.startswith(prefix):
            matches.append(
                ls.LabelingScopeEnv(
                    ctx=ctx,
                    ref_root=ctx.references_dir.resolve(),
                    area_path=ctx.area_path,
                    references_prefix=ctx.references_prefix.rstrip("/"),
                )
            )
    if matches:
        matches.sort(key=lambda item: len(item.references_prefix), reverse=True)
        return matches[0]
    return env


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


def _entry_regions(entry: dict[str, Any]) -> list[dict[str, Any]]:
    raw_regs = entry.get("regions")
    if isinstance(raw_regs, list):
        return [r for r in raw_regs if isinstance(r, dict)]
    return []


def get_labeling_document(
    ref_rel: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    area_doc = ls.load_area_doc(env)

    abs_png = (env.repo_root / ref_rel).resolve()
    if not abs_png.is_file():
        msg = f"reference not found: {ref_rel}"
        raise FileNotFoundError(msg)

    doc = area_doc
    found = ls.entry_for_ref(doc, ref_rel, env)
    regions: list[dict[str, Any]] = []
    screen_id = ""
    entry_id: int | None = None
    if found is not None:
        entry_id, entry = found
        screen_id = str(entry.get("screen_id") or "")
        regions = _entry_regions(entry)

    basename_stem = reference_basename_stem(ls.rel_under_ref_root(ref_rel, env))

    return {
        "ref": ref_rel,
        "display_ref": ref_rel,
        "screen_id": screen_id,
        "entry_id": entry_id,
        "regions": regions,
        "is_pending": ls.is_pending_temporal_ref(ref_rel, env),
        "basename": basename_stem,
        "area_path": (
            str(env.area_path.relative_to(env.repo_root)) if env.area_path is not None else None
        ),
        "references_prefix": env.references_prefix,
        "scope": normalize_module_scope(scope),
        "module_key": env.ctx.storage_key,
        "module_title": env.ctx.title,
    }


def save_labeling_regions(
    ref_rel: str,
    regions: list[dict[str, Any]],
    *,
    screen_id: str | None = None,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    abs_png = (env.repo_root / ref_rel).resolve()
    if not abs_png.is_file():
        msg = f"reference not found: {ref_rel}"
        raise FileNotFoundError(msg)
    env = _write_env_for_reference(env, ref_rel)

    doc = ls.load_area_doc(env)
    screens = doc.setdefault("screens", [])
    if not isinstance(screens, list):
        screens = []
        doc["screens"] = screens

    found = ls.entry_for_ref(doc, ref_rel, env)
    if found is None:
        sid_clean = (screen_id or "").strip()
        if not regions and not sid_clean:
            return {
                "ok": True,
                "region_count": 0,
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

    old_regions = _entry_regions(entry)
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

    entry["regions"] = regions_to_save
    if screen_id is not None:
        entry["screen_id"] = str(screen_id).strip()
    screens[idx] = entry
    _atomic_write_json(_require_writable_area_path(env), doc)
    crop_meta = _export_module_crops(doc, env)
    _publish_area_manifest_changed()
    return {
        "ok": True,
        "region_count": len(regions_to_save),
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
    """Capture a fresh ADB screenshot into ``<prefix>/temporal/<shot>.png``."""
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
    """Overwrite an existing reference PNG with a fresh ADB screenshot."""
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


def delete_reference(
    ref_rel: str,
    *,
    scope: str = CORE_MODULE_KEY,
) -> dict[str, Any]:
    """Delete a published reference PNG, its area.json entry, and matching crops.

    Pending temporal captures should use :func:`discard_pending_capture` instead;
    rolling preview frames (``*_current_state.png``) are refused outright.
    """
    env = ls.scope_env(scope)
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    if not ref_rel or ".." in Path(ref_rel).parts:
        msg = "invalid path"
        raise ValueError(msg)
    path = (env.repo_root / ref_rel).resolve()
    if _is_rolling_preview_png(path, env.ref_root):
        msg = "cannot delete rolling preview files"
        raise ValueError(msg)
    if ls.is_pending_temporal_ref(ref_rel, env):
        return discard_pending_capture(ref_rel, scope=scope)
    if not path.is_file():
        msg = f"reference not found: {ref_rel}"
        raise FileNotFoundError(msg)

    # Remove the PNG.
    path.unlink()

    # Strip the matching area.json screen entry, if any.
    doc = ls.load_area_doc(env)
    found = ls.entry_for_ref(doc, ref_rel, env)
    screens_removed = 0
    if found is not None:
        idx, _entry = found
        screens = doc.setdefault("screens", [])
        if isinstance(screens, list) and 0 <= idx < len(screens):
            screens.pop(idx)
            screens_removed = 1
            _atomic_write_json(_require_writable_area_path(env), doc)

    # Best-effort cleanup of region crops named ``<ref-stem>_<region>.png``.
    crops_removed: list[str] = []
    crop_dir = env.ref_root / "crop"
    if crop_dir.is_dir():
        stem = path.stem
        for crop in crop_dir.glob(f"{stem}_*.png"):
            try:
                crop.unlink()
                crops_removed.append(crop.name)
            except OSError as exc:
                logger.warning("delete_reference: failed to remove crop %s: %s", crop, exc)

    return {
        "ok": True,
        "ref": ref_rel,
        "screens_removed": screens_removed,
        "crops_removed": crops_removed,
    }


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
    env = _write_env_for_reference(env, ref_rel)
    if not ls.is_pending_temporal_ref(ref_rel, env):
        msg = f"only pending captures under {env.references_prefix}/{TEMPORAL_SUBDIR}/ can be promoted"
        raise ValueError(msg)
    src = (env.repo_root / ref_rel).resolve()
    if not src.is_file():
        msg = f"Source missing: `{src.name}`."
        raise FileNotFoundError(msg)
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
