"""Module scope helpers for the labeling API (mirrors Streamlit wiki_module)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config.module_registry import (
    ALL_MODULES_KEY,
    CORE_MODULE_KEY,
    WikiModuleContext,
    all_modules_context,
    list_labeling_modules,
    merge_all_area_docs,
    normalize_module_scope,
    ocr_path_belongs_to_context,
)
from config.paths import repo_root
from config.reference_naming import TEMPORAL_SUBDIR
from dashboard.area_doc import default_area_doc, load_json
from dashboard.reference_ocr_paths import resolve_ocr_path_in_reference_context

_REPO = repo_root()


@dataclass(frozen=True)
class LabelingScopeEnv:
    ctx: WikiModuleContext
    ref_root: Path
    area_path: Path | None
    references_prefix: str

    @property
    def repo_root(self) -> Path:
        return self.ctx.repo_root.resolve()


def _request_game(explicit: str | None) -> str:
    if explicit:
        return explicit
    from api.services.game_resolver import current_request_game

    return current_request_game()


def context_for_scope(
    scope: str | None,
    *,
    game: str | None = None,
) -> WikiModuleContext:
    g = _request_game(game)
    key = normalize_module_scope(scope)
    if key in (ALL_MODULES_KEY, CORE_MODULE_KEY):
        return all_modules_context(_REPO)
    for ctx in list_labeling_modules(_REPO, game=g):
        if ctx.storage_key == key or ctx.module_id == key:
            return ctx
        sk = ctx.storage_key
        if ":" in sk and sk.split(":", 1)[1] == key:
            return ctx
    return all_modules_context(_REPO)


def scope_env(scope: str | None, *, game: str | None = None) -> LabelingScopeEnv:
    ctx = context_for_scope(scope, game=game)
    return LabelingScopeEnv(
        ctx=ctx,
        ref_root=ctx.references_dir.resolve(),
        area_path=ctx.area_path,
        references_prefix=ctx.references_prefix.rstrip("/"),
    )


def list_labeling_scopes(*, game: str | None = None) -> list[dict[str, Any]]:
    root = _REPO.resolve()
    g = _request_game(game)
    ctxs: list[WikiModuleContext] = [all_modules_context(root)]
    ctxs.extend(c for c in list_labeling_modules(root, game=g) if c.module_id is not None)
    out: list[dict[str, Any]] = []
    for ctx in ctxs:
        default_ref = (ctx.default_ref or "").replace("\\", "/").strip().lstrip("/")
        out.append(
            {
                "key": ctx.storage_key,
                "title": ctx.title,
                "label": f"{ctx.title} ({ctx.storage_key})"
                if ctx.module_id is not None
                else ctx.title,
                "references_prefix": ctx.references_prefix,
                "area_path": (
                    str(ctx.area_path.relative_to(root)) if ctx.area_path is not None else None
                ),
                "default_ref": default_ref or None,
                "is_all": ctx.is_all,
            }
        )
    return out


def load_area_doc(env: LabelingScopeEnv) -> dict[str, Any]:
    if env.ctx.is_all:
        return merge_all_area_docs(env.repo_root)
    if env.area_path is not None and env.area_path.is_file():
        doc = load_json(env.area_path)
        if isinstance(doc, dict):
            doc.setdefault("version", 2)
            screens = doc.get("screens")
            if not isinstance(screens, list):
                doc["screens"] = []
            return doc
    return default_area_doc([])


def rel_under_ref_root(ref_rel: str, env: LabelingScopeEnv) -> str:
    s = ref_rel.replace("\\", "/").strip().lstrip("/")
    prefix = env.references_prefix + "/"
    if s.startswith(prefix):
        return s[len(prefix) :]
    if s.startswith("references/"):
        return s.split("references/", 1)[1]
    return s


def repo_ref_for_under(rel_under: str, env: LabelingScopeEnv) -> str:
    tail = rel_under.replace("\\", "/").strip().lstrip("/")
    return f"{env.references_prefix}/{tail}".replace("\\", "/")


def is_pending_temporal_ref(ref_rel: str, env: LabelingScopeEnv) -> bool:
    rel = rel_under_ref_root(ref_rel, env)
    return rel.startswith(f"{TEMPORAL_SUBDIR}/") and not rel.endswith("_current_state.png")


def entry_for_ref(
    doc: dict[str, Any],
    ref_rel: str,
    env: LabelingScopeEnv,
) -> tuple[int, dict[str, Any]] | None:
    ref_rel = ref_rel.replace("\\", "/").strip().lstrip("/")
    try:
        ref_abs = (env.repo_root / ref_rel).resolve()
    except OSError:
        return None
    screens = doc.get("screens") or []
    for idx, entry in enumerate(screens):
        if not isinstance(entry, dict):
            continue
        ocr = str(entry.get("ocr") or "").replace("\\", "/").strip()
        if not ocr or not ocr_path_belongs_to_context(ocr, env.ctx):
            continue
        try:
            ocr_abs = resolve_ocr_path_in_reference_context(
                ocr,
                env.references_prefix,
                repo_root_path=env.repo_root,
            ).resolve()
        except OSError:
            continue
        if ocr_abs == ref_abs:
            return idx, entry
        try:
            if ocr_abs.name == ref_abs.name and ocr.endswith(ref_abs.name):
                return idx, entry
        except OSError:
            pass
    # Legacy tail match
    short = rel_under_ref_root(ref_rel, env)
    for idx, entry in enumerate(screens):
        if not isinstance(entry, dict):
            continue
        ocr = str(entry.get("ocr") or "").replace("\\", "/").strip()
        if not ocr:
            continue
        if ocr.endswith(short) or short.endswith(Path(ocr).name):
            return idx, entry
    return None
