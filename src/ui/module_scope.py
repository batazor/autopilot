"""Shared module scope selector (All / Core / feature modules) for UI pages."""
from __future__ import annotations

import streamlit as st

from config.module_registry import (
    ALL_MODULES_KEY,
    module_scope_options,
    normalize_module_scope,
)
from ui.area_annotator import REPO_ROOT

_QUERY_PARAM = "module"
_STORAGE_KEY = "module_scope_storage_key"


def module_scope_from_query() -> str | None:
    raw = st.query_params.get(_QUERY_PARAM)
    if raw is None:
        return None
    if isinstance(raw, list):
        return str(raw[0]).strip() if raw else None
    return str(raw).strip() or None


def render_module_scope_selector(
    *,
    sidebar: bool = True,
    in_sidebar: bool = False,
    help: str | None = None,
) -> str:
    """Return active scope key: ``all`` | ``core`` | ``<module_id>``."""

    opts = module_scope_options(REPO_ROOT)
    keys = [k for k, _ in opts]
    labels = [label for _, label in opts]
    qp = module_scope_from_query()
    default_key = normalize_module_scope(
        qp if qp else str(st.session_state.get(_STORAGE_KEY) or ALL_MODULES_KEY)
    )
    if default_key not in keys:
        default_key = ALL_MODULES_KEY
    selectbox_kwargs = dict(
        label="Module",
        options=labels,
        index=keys.index(default_key),
        key="module_scope_select",
        help=help
        or "All — core + every feature module. Core — repo root only. "
        "A module — only that module's scenarios, overlay rules, wiki, and areas.",
    )
    if in_sidebar:
        # Caller already opened ``with st.sidebar:`` — render in that context.
        picked = st.selectbox(**selectbox_kwargs)
    else:
        container = st.sidebar if sidebar else st
        with container:
            picked = st.selectbox(**selectbox_kwargs)
    scope = keys[labels.index(picked)]
    if qp != scope:
        st.query_params[_QUERY_PARAM] = scope
        st.session_state[_STORAGE_KEY] = scope
        st.rerun()
    st.session_state[_STORAGE_KEY] = scope
    return scope
