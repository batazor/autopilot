"""Streamlit module selector for Gallery / Labeling (core vs ``modules/<id>/``)."""
from __future__ import annotations

import contextlib
from pathlib import Path

import streamlit as st

from config.module_registry import (
    ALL_MODULES_KEY,
    WikiModuleContext,
    list_labeling_modules,
    normalize_module_scope,
)
from ui.area_annotator import REPO_ROOT, default_area_doc, load_json
from ui.keys import (
    AREA_DOC,
    CANVAS_LAST_SIG,
    CANVAS_REV,
    ENTRY_IDX,
    LABELING_BN_SYNC_SEL,
    LABELING_TREE_SELECTION,
    WIKI_MODULE_STORAGE_KEY,
)

_QUERY_PARAM = "module"


def wiki_module_from_query() -> str | None:
    raw = st.query_params.get(_QUERY_PARAM)
    if raw is None:
        return None
    if isinstance(raw, list):
        return str(raw[0]).strip() if raw else None
    return str(raw).strip() or None


def _module_labels(ctxs: list[WikiModuleContext]) -> list[str]:
    return [f"{c.title} ({c.storage_key})" for c in ctxs]


def _index_for_key(ctxs: list[WikiModuleContext], key: str) -> int:
    scope = normalize_module_scope(key)
    for i, c in enumerate(ctxs):
        if c.storage_key == scope or c.module_id == scope:
            return i
    return 0


def _selector_contexts() -> list[WikiModuleContext]:
    """All, Core, then every module (for Gallery / Labeling).

    Uses ``list_labeling_modules`` so that modules with ``wiki: false`` still
    appear in the Labeling UI — ``wiki`` controls only the wiki picker, not
    the area-annotation workflow.
    """

    from config.module_registry import all_modules_context, core_module_context

    root = REPO_ROOT
    out: list[WikiModuleContext] = [all_modules_context(root), core_module_context(root)]
    out.extend(ctx for ctx in list_labeling_modules(root) if ctx.module_id is not None)
    return out


def ensure_wiki_module_session(ctx: WikiModuleContext) -> None:
    """Reload ``area_doc`` when the user switches module scope."""
    prev = st.session_state.get(WIKI_MODULE_STORAGE_KEY)
    if prev == ctx.storage_key and AREA_DOC in st.session_state:
        return
    st.session_state[WIKI_MODULE_STORAGE_KEY] = ctx.storage_key
    st.session_state["_wiki_area_path"] = str(ctx.area_path)
    st.session_state["_wiki_references_prefix"] = ctx.references_prefix
    try:
        if ctx.is_all:
            from config.module_registry import merge_all_area_docs

            st.session_state[AREA_DOC] = merge_all_area_docs(ctx.repo_root)
        elif ctx.area_path.is_file():
            st.session_state[AREA_DOC] = load_json(ctx.area_path)
        else:
            st.session_state[AREA_DOC] = default_area_doc([])
    except (OSError, ValueError) as exc:
        st.session_state[AREA_DOC] = default_area_doc([])
        st.session_state["load_error"] = str(exc)
    st.session_state[ENTRY_IDX] = 0
    st.session_state.pop(LABELING_TREE_SELECTION, None)
    st.session_state.pop(LABELING_BN_SYNC_SEL, None)
    st.session_state[CANVAS_REV] = int(st.session_state.get(CANVAS_REV, 0)) + 1
    st.session_state[CANVAS_LAST_SIG] = ""


def active_wiki_area_path() -> Path:
    raw = st.session_state.get("_wiki_area_path")
    if isinstance(raw, str) and raw.strip():
        return Path(raw)
    return REPO_ROOT / "area.json"


def active_references_prefix() -> str:
    raw = st.session_state.get("_wiki_references_prefix")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().rstrip("/")
    return "references"


def render_wiki_module_selector(*, help: str | None = None) -> WikiModuleContext:  # noqa: A002 — mirrors streamlit's parameter name
    """Sidebar or inline selectbox; persists ``?module=`` query param."""
    ctxs = _selector_contexts()
    qp = wiki_module_from_query()
    default_key = qp or str(st.session_state.get(WIKI_MODULE_STORAGE_KEY) or ALL_MODULES_KEY)
    labels = _module_labels(ctxs)
    picked = st.selectbox(
        "Module",
        options=labels,
        index=_index_for_key(ctxs, default_key),
        key="wiki_module_select",
        help=help
        or "Core uses root ``area.json`` and ``references/``. "
        "Modules use ``modules/<id>/area.yaml`` and their own references tree when configured.",
    )
    ctx = ctxs[labels.index(picked)]
    prev_scope = st.session_state.get(WIKI_MODULE_STORAGE_KEY)
    scope_changed = prev_scope is not None and prev_scope != ctx.storage_key
    if qp != ctx.query_value:
        with st.spinner("Switching module…"):
            st.query_params[_QUERY_PARAM] = ctx.query_value
            if not _has_repo_relative_reference_query(ctx.repo_root):
                _sync_labeling_ref_for_module(ctx)
            ensure_wiki_module_session(ctx)
            st.rerun()
    if scope_changed:
        _sync_labeling_ref_for_module(ctx)
    ensure_wiki_module_session(ctx)
    return ctx


def _sync_labeling_ref_for_module(ctx: WikiModuleContext) -> None:
    """Reset stale `?ref=` when the Labeling/Gallery module scope changes."""
    with contextlib.suppress(Exception):
        default_ref = (ctx.default_ref or "").replace("\\", "/").strip().lstrip("/")
        if default_ref and not default_ref.startswith("..") and "/.." not in default_ref:
            st.query_params["ref"] = default_ref
        elif st.query_params.get("ref"):
            del st.query_params["ref"]
        if st.query_params.get("version"):
            del st.query_params["version"]


def _has_repo_relative_reference_query(repo_root: Path) -> bool:
    """Whether `?ref=` already points at a concrete repo-local reference PNG."""

    raw = st.query_params.get("ref")
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    rel = str(raw or "").replace("\\", "/").strip().lstrip("/")
    if not rel or rel.startswith("..") or "/.." in rel:
        return False
    if "/references/" not in rel and not rel.startswith("references/"):
        return False
    path = (repo_root / rel).resolve()
    try:
        path.relative_to(repo_root.resolve())
    except ValueError:
        return False
    return path.is_file() and path.suffix.lower() == ".png"
