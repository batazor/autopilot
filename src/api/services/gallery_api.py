"""Reference gallery API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from config.module_registry import (
    ALL_MODULES_KEY,
    CORE_MODULE_KEY,
    WikiModuleContext,
    all_modules_context,
    core_module_context,
    list_labeling_modules,
    merge_all_area_docs,
    normalize_module_scope,
)
from config.paths import repo_root
from ui.area_annotator import load_json
from ui.reference_preview import list_reference_pngs

_REPO = repo_root()


def _context_for_scope(scope: str) -> WikiModuleContext:
    scope = normalize_module_scope(scope)
    if scope == ALL_MODULES_KEY:
        return all_modules_context(_REPO)
    if scope == CORE_MODULE_KEY:
        return core_module_context(_REPO)
    for ctx in list_labeling_modules(_REPO):
        if ctx.storage_key == scope or ctx.module_id == scope:
            return ctx
    return all_modules_context(_REPO)


def _area_doc_for_context(ctx: WikiModuleContext) -> tuple[dict[str, Any], str]:
    if ctx.is_all:
        return merge_all_area_docs(ctx.repo_root), ctx.references_prefix
    doc = load_json(ctx.area_path) if ctx.area_path.is_file() else {}
    return doc if isinstance(doc, dict) else {}, ctx.references_prefix


def _refs_for_png(rel: str, area_doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for entry in area_doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        ocr = str(entry.get("ocr") or "").replace("\\", "/").strip()
        if not ocr:
            continue
        if ocr.endswith(rel) or rel.endswith(Path(ocr).name):
            sid = str(entry.get("screen_id") or "").strip()
            if sid:
                out.append(sid)
    return out


def list_gallery(*, scope: str = "all", query: str = "") -> dict[str, Any]:
    ctx = _context_for_scope(scope)
    area_doc, ref_prefix = _area_doc_for_context(ctx)
    ref_root = (_REPO / ref_prefix).resolve()
    paths = list_reference_pngs(limit=500, root=ref_root, exclude_temporal=True, exclude_crop=True)
    q = query.strip().lower()
    items: list[dict[str, Any]] = []
    prefix_slash = ref_prefix.rstrip("/") + "/"
    for p in paths:
        try:
            rel = p.relative_to(_REPO).as_posix()
        except ValueError:
            continue
        if not ctx.is_all and not rel.startswith(prefix_slash):
            continue
        tail = rel.split(prefix_slash, 1)[-1] if prefix_slash in rel else rel
        sids = _refs_for_png(tail, area_doc)
        group = sids[0] if sids else _node_group(rel)
        hay = f"{rel} {' '.join(sids)}".lower()
        if q and q not in hay:
            continue
        items.append(
            {
                "rel": rel,
                "name": p.name,
                "group": group,
                "screen_ids": sids,
                "size_bytes": p.stat().st_size,
            }
        )
    return {"scope": scope, "items": items, "count": len(items)}


def _node_group(rel: str) -> str:
    parts = rel.replace("\\", "/").split("/")
    if len(parts) >= 2 and parts[0] == "references" and len(parts) > 2:
        return parts[1]
    if len(parts) >= 2:
        return parts[0]
    return "(unassigned)"


def read_gallery_image(rel: str) -> bytes:
    rel = rel.replace("\\", "/").strip().lstrip("/")
    if ".." in Path(rel).parts:
        msg = "invalid path"
        raise ValueError(msg)
    path = (_REPO / rel).resolve()
    if not path.is_file() or path.suffix.lower() != ".png":
        msg = "not found"
        raise FileNotFoundError(msg)
    return path.read_bytes()
