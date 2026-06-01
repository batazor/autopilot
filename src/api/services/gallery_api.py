"""Reference gallery API."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from config.module_discovery import is_core_nested_module
from config.module_registry import (
    ALL_MODULES_KEY,
    CORE_MODULE_KEY,
    WikiModuleContext,
    all_modules_context,
    list_labeling_modules,
    merge_all_area_docs,
    normalize_module_scope,
)
from config.paths import repo_root
from dashboard.area_doc import load_json
from dashboard.reference_preview import list_reference_pngs

_REPO = repo_root()


def _request_game() -> str:
    """Active game for the current request (set by ``request_game`` dependency)."""
    from api.services.game_resolver import current_request_game

    return current_request_game()


def _context_for_scope(scope: str, *, game: str) -> WikiModuleContext:
    scope = normalize_module_scope(scope)
    if scope in (ALL_MODULES_KEY, CORE_MODULE_KEY):
        return all_modules_context(_REPO, game=game)
    for ctx in list_labeling_modules(_REPO, game=game):
        if ctx.storage_key == scope or ctx.module_id == scope:
            return ctx
    return all_modules_context(_REPO, game=game)


def _area_doc_for_context(ctx: WikiModuleContext) -> tuple[dict[str, Any], str]:
    if ctx.is_all:
        return merge_all_area_docs(ctx.repo_root, game=ctx.game), ctx.references_prefix
    doc = load_json(ctx.area_path) if ctx.area_path is not None and ctx.area_path.is_file() else {}
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


def _collect_reference_roots(ctx: WikiModuleContext, *, game: str) -> list[Path]:
    """Aggregate references_dir across modules for ``all`` / ``core`` scopes.

    Only the active ``game``'s modules are walked, so the "All"/"Core" scopes
    show that game's references rather than every game's.
    """
    roots: list[Path] = []
    seen: set[Path] = set()

    def _push(p: Path) -> None:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            roots.append(r)

    if ctx.is_all:
        for mctx in list_labeling_modules(_REPO, game=game):
            _push(mctx.references_dir)
        return roots
    if ctx.module_id is None:
        _push(ctx.references_dir)
        for mctx in list_labeling_modules(_REPO, game=game):
            if mctx.module_dir is not None and is_core_nested_module(mctx.module_dir, _REPO):
                _push(mctx.references_dir)
        return roots
    _push(ctx.references_dir)
    return roots


def list_gallery(*, scope: str = "all", query: str = "") -> dict[str, Any]:
    game = _request_game()
    ctx = _context_for_scope(scope, game=game)
    area_doc, ref_prefix = _area_doc_for_context(ctx)
    roots = _collect_reference_roots(ctx, game=game)
    seen_paths: set[Path] = set()
    paths: list[Path] = []
    for r in roots:
        for p in list_reference_pngs(limit=500, root=r, exclude_temporal=True, exclude_crop=True):
            if p not in seen_paths:
                seen_paths.add(p)
                paths.append(p)
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    paths = paths[:500]
    q = query.strip().lower()
    items: list[dict[str, Any]] = []
    prefix_slash = ref_prefix.rstrip("/") + "/"
    for p in paths:
        try:
            rel = p.relative_to(_REPO).as_posix()
        except ValueError:
            continue
        if (not ctx.is_all and ctx.module_id is not None) and not rel.startswith(prefix_slash):
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
    from config.games import split_repo_relative

    split = split_repo_relative(rel)
    if split is not None:
        module_id, tail = split
        # Use the last segment of module_id as the group label (preserves the
        # pre-Phase 3 behaviour where the group was the first segment after
        # ``modules/``).
        return module_id.rsplit("/", 1)[-1] if not tail else module_id
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
