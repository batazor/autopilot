"""Rename reference PNGs by basename (Labeling + MCP).

Mirrors ``ui.reference_preview.rename_reference_to_basename`` and
``ui.reference_area_sync.sync_area_json_ocr_after_reference_rename``, with module-aware
``references/`` trees and optional crop file renames.
"""
from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.reference_naming import reference_file_basename
from dashboard.labeling_helpers import suggest_basename_from_entry
from dashboard.reference_area_sync import sync_area_json_ocr_after_reference_rename
from dashboard.reference_preview import rename_reference_to_basename


@dataclass(frozen=True)
class ReferencesContext:
    repo_root: Path
    ref_root: Path
    references_prefix: str
    area_path: Path


def resolve_references_context(repo_root: Path, png_repo_rel: str) -> ReferencesContext:
    """Resolve references root, area manifest, and prefix for a repo-relative PNG path."""

    rel = str(png_repo_rel or "").replace("\\", "/").strip().lstrip("/")
    if not rel or ".." in Path(rel).parts:
        msg = "source must be a repo-relative path without '..'"
        raise ValueError(msg)
    if Path(rel).suffix.lower() != ".png":
        msg = "source must be a .png reference screenshot"
        raise ValueError(msg)

    parts = Path(rel).parts
    try:
        ref_idx = parts.index("references")
    except ValueError as exc:
        msg = "source must live under a references/ directory"
        raise ValueError(msg) from exc

    references_prefix = "/".join(parts[: ref_idx + 1])
    repo_root = repo_root.resolve()
    ref_root = (repo_root / references_prefix).resolve()
    abs_png = (repo_root / rel).resolve()
    if not abs_png.is_file():
        msg = f"reference PNG not found: {rel}"
        raise FileNotFoundError(msg)
    try:
        abs_png.relative_to(ref_root)
    except ValueError as exc:
        msg = f"path is not under {references_prefix}/"
        raise ValueError(msg) from exc

    if parts[0] == "modules" and len(parts) > ref_idx + 1:
        module_root = repo_root.joinpath(*parts[:ref_idx])
        area_path = _first_existing_area(module_root)
    else:
        area_path = repo_root / "area.json"

    return ReferencesContext(
        repo_root=repo_root,
        ref_root=ref_root,
        references_prefix=references_prefix,
        area_path=area_path,
    )


def _first_existing_area(module_root: Path) -> Path:
    for name in ("area.yaml", "area.yml", "area.json"):
        candidate = module_root / name
        if candidate.is_file():
            return candidate
    return module_root / "area.yaml"


def rel_under_references(ctx: ReferencesContext, png_repo_rel: str) -> str:
    rel = str(png_repo_rel or "").replace("\\", "/").strip().lstrip("/")
    return Path(rel).relative_to(ctx.references_prefix).as_posix()


def normalize_reference_basename(raw: str, instance_id: str = "") -> str:
    """Sanitize a basename (no ``.png`` suffix) using the same rules as Labeling."""

    return reference_file_basename(str(raw or "").strip(), str(instance_id or ""))


def suggest_reference_basename(
    repo_root: Path,
    *,
    source_repo_rel: str,
    instance_id: str = "",
    screen_id: str | None = None,
) -> dict[str, Any]:
    """Suggest a stable basename from ``area`` metadata for the given reference PNG."""

    ctx = resolve_references_context(repo_root, source_repo_rel)
    old_rel = rel_under_references(ctx, source_repo_rel)
    current_stem = Path(old_rel).stem

    entry = _area_entry_for_ref(ctx, old_rel, screen_id=screen_id)
    suggested = suggest_basename_from_entry(entry, instance_id) if entry else None
    return {
        "source": source_repo_rel.replace("\\", "/"),
        "references_prefix": ctx.references_prefix,
        "area_path": ctx.area_path.relative_to(ctx.repo_root).as_posix(),
        "current_basename": current_stem,
        "suggested_basename": suggested,
        "screen_id": str(entry.get("screen_id") or "").strip() if entry else None,
    }


def rename_reference_basename(
    repo_root: Path,
    *,
    source_repo_rel: str,
    basename: str,
    instance_id: str = "",
    sync_area: bool = True,
    rename_crops: bool = True,
) -> dict[str, Any]:
    """Rename a reference PNG and optionally sync ``area`` ``ocr`` paths and crop tiles."""

    ctx = resolve_references_context(repo_root, source_repo_rel)
    old_rel = rel_under_references(ctx, source_repo_rel)
    old_stem = Path(old_rel).stem
    dest_base = normalize_reference_basename(basename, instance_id)
    new_rel = f"{dest_base}.png"

    if old_rel.replace("\\", "/") == new_rel:
        return {
            "ok": True,
            "unchanged": True,
            "message": f"Already `{dest_base}.png`.",
            "source": source_repo_rel.replace("\\", "/"),
            "old_rel": old_rel,
            "new_rel": new_rel,
            "references_prefix": ctx.references_prefix,
            "area_path": ctx.area_path.relative_to(ctx.repo_root).as_posix(),
            "area_ocr_entries_updated": 0,
            "crops_renamed": [],
        }

    src = ctx.ref_root / old_rel
    ok, msg = rename_reference_to_basename(
        src,
        dest_base,
        instance_id or "mcp",
        references_dir=ctx.ref_root,
    )
    if not ok:
        return {
            "ok": False,
            "unchanged": False,
            "message": msg,
            "source": source_repo_rel.replace("\\", "/"),
            "old_rel": old_rel,
            "new_rel": new_rel,
            "references_prefix": ctx.references_prefix,
            "area_path": ctx.area_path.relative_to(ctx.repo_root).as_posix(),
            "area_ocr_entries_updated": 0,
            "crops_renamed": [],
        }

    crops_renamed: list[str] = []
    if rename_crops:
        crops_renamed = _rename_crop_files(ctx.ref_root, old_stem, dest_base)

    area_updated = 0
    sync_err = ""
    if sync_area and ctx.area_path.is_file():
        sync_ok, sync_err, area_updated = sync_area_json_ocr_after_reference_rename(
            ctx.repo_root,
            old_rel_under_refs=old_rel,
            new_rel_under_refs=new_rel,
            area_path=ctx.area_path,
            references_prefix=ctx.references_prefix,
        )
        if not sync_ok:
            _rollback_rename(ctx.ref_root, old_rel, new_rel, crops_renamed, old_stem, dest_base)
            return {
                "ok": False,
                "unchanged": False,
                "message": f"Rename rolled back — area sync failed: {sync_err}",
                "source": source_repo_rel.replace("\\", "/"),
                "old_rel": old_rel,
                "new_rel": new_rel,
                "references_prefix": ctx.references_prefix,
                "area_path": ctx.area_path.relative_to(ctx.repo_root).as_posix(),
                "area_ocr_entries_updated": 0,
                "crops_renamed": [],
            }

    out_msg = msg
    if area_updated:
        out_msg += f" · Updated area ({area_updated} ocr path(s))."
    if crops_renamed:
        out_msg += f" · Renamed {len(crops_renamed)} crop(s)."

    return {
        "ok": True,
        "unchanged": False,
        "message": out_msg,
        "source": source_repo_rel.replace("\\", "/"),
        "old_rel": old_rel,
        "new_rel": new_rel,
        "new_path": f"{ctx.references_prefix}/{new_rel}",
        "basename": dest_base,
        "references_prefix": ctx.references_prefix,
        "area_path": ctx.area_path.relative_to(ctx.repo_root).as_posix(),
        "area_ocr_entries_updated": area_updated,
        "crops_renamed": crops_renamed,
    }


def _area_entry_for_ref(
    ctx: ReferencesContext,
    rel_under_refs: str,
    *,
    screen_id: str | None,
) -> dict[str, Any] | None:
    if not ctx.area_path.is_file():
        return None
    try:
        from dashboard.area_doc import load_json

        doc = load_json(ctx.area_path)
    except (OSError, ValueError):
        doc = None
    if not isinstance(doc, dict):
        return None

    target = rel_under_refs.replace("\\", "/")
    sid_filter = str(screen_id or "").strip()
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        ocr = str(entry.get("ocr") or "").replace("\\", "/").strip()
        if not ocr:
            continue
        ocr_rel = ocr
        prefix = ctx.references_prefix.rstrip("/") + "/"
        if ocr.startswith(prefix):
            ocr_rel = ocr[len(prefix) :]
        elif ocr.startswith("references/"):
            try:
                ocr_rel = Path(ocr).relative_to("references").as_posix()
            except ValueError:
                ocr_rel = Path(ocr).name
        if ocr_rel.replace("\\", "/") != target:
            continue
        if sid_filter and str(entry.get("screen_id") or "").strip() != sid_filter:
            continue
        return entry
    return None


def _rename_crop_files(ref_root: Path, old_stem: str, new_stem: str) -> list[str]:
    if old_stem == new_stem:
        return []
    crop_dir = ref_root / "crop"
    if not crop_dir.is_dir():
        return []
    renamed: list[str] = []
    prefix = f"{old_stem}_"
    for src in sorted(crop_dir.glob(f"{old_stem}_*.png")):
        suffix = src.name[len(prefix) :]
        dest = crop_dir / f"{new_stem}_{suffix}"
        if dest.is_file() and dest.resolve() != src.resolve():
            continue
        src.rename(dest)
        renamed.append(dest.relative_to(ref_root).as_posix())
    return renamed


def _rollback_rename(
    ref_root: Path,
    old_rel: str,
    new_rel: str,
    crops_renamed: list[str],
    old_stem: str,
    new_stem: str,
) -> None:
    new_path = ref_root / new_rel
    old_path = ref_root / old_rel
    if new_path.is_file():
        with contextlib.suppress(OSError):
            new_path.rename(old_path)
    for crop_rel in crops_renamed:
        p = ref_root / crop_rel
        if p.is_file():
            restored = ref_root / "crop" / f"{old_stem}_{p.name[len(new_stem) + 1 :]}"
            with contextlib.suppress(OSError):
                p.rename(restored)
