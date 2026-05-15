"""Streamlit module selector for Gallery / Labeling (core vs ``modules/<id>/``)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from config.module_registry import WikiModuleContext, get_wiki_module, list_wiki_modules
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
    for i, c in enumerate(ctxs):
        if c.storage_key == key:
            return i
    return 0


def ensure_wiki_module_session(ctx: WikiModuleContext) -> None:
    """Reload ``area_doc`` when the user switches module scope."""
    prev = st.session_state.get(WIKI_MODULE_STORAGE_KEY)
    if prev == ctx.storage_key and AREA_DOC in st.session_state:
        return
    st.session_state[WIKI_MODULE_STORAGE_KEY] = ctx.storage_key
    st.session_state["_wiki_area_path"] = str(ctx.area_path)
    st.session_state["_wiki_references_prefix"] = ctx.references_prefix
    try:
        if ctx.area_path.is_file():
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


def render_wiki_module_selector(*, help: str | None = None) -> WikiModuleContext:
    """Sidebar or inline selectbox; persists ``?module=`` query param."""
    ctxs = list_wiki_modules(REPO_ROOT)
    qp = wiki_module_from_query()
    default_key = qp if qp else str(st.session_state.get(WIKI_MODULE_STORAGE_KEY) or "core")
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
    if qp != ctx.query_value:
        with st.spinner("Switching module…"):
            st.query_params[_QUERY_PARAM] = ctx.query_value
            ensure_wiki_module_session(ctx)
            st.rerun()
    ensure_wiki_module_session(ctx)
    return ctx
