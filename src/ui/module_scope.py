"""Shared module scope selector (All / Core / feature modules) for UI pages.

Renders as a hierarchical tree (``st_ant_tree``) grouped by module namespace:
``core/...`` and ``events/...`` collapse under their respective group node;
top-level modules (no ``/`` in the id) appear as plain leaves alongside
``All`` and ``Core``. Easier to navigate than the flat selectbox once the
module set grows past a handful of entries.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
from st_ant_tree import st_ant_tree

from config.module_registry import (
    ALL_MODULES_KEY,
    CORE_MODULE_KEY,
    module_scope_options,
    normalize_module_scope,
)
from ui.area_annotator import REPO_ROOT

_QUERY_PARAM = "module"
_STORAGE_KEY = "module_scope_storage_key"
_DIR_PREFIX = "__dir__/"


def module_scope_from_query() -> str | None:
    raw = st.query_params.get(_QUERY_PARAM)
    if raw is None:
        return None
    if isinstance(raw, list):
        return str(raw[0]).strip() if raw else None
    return str(raw).strip() or None


def build_module_scope_tree(
    options: list[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Pure helper: turn ``[(storage_key, label), ...]`` into ``treeData``.

    Grouping rule: when ``storage_key`` contains ``/`` (e.g.
    ``core/chief_profile``), the prefix becomes a non-selectable group node
    and the suffix becomes a leaf under it. Reserved keys (``all``, ``core``)
    and slash-less module ids stay as top-level leaves. The group node's
    ``value`` is prefixed with ``__dir__/`` so the picker can distinguish
    placeholder clicks from real selections.

    Output order: top-level leaves (``All``, ``Core``, slash-less modules)
    in input order, followed by group nodes alphabetised by namespace.
    """
    top_level: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for key, label in options:
        if "/" in key and key not in (ALL_MODULES_KEY, CORE_MODULE_KEY):
            namespace, _, _ = key.partition("/")
            grouped.setdefault(namespace, []).append(
                {"value": key, "title": label}
            )
        else:
            top_level.append({"value": key, "title": label})

    nodes: list[dict[str, Any]] = list(top_level)
    nodes.extend(
        {
            "value": f"{_DIR_PREFIX}{namespace}",
            "title": f"{namespace}/",
            "selectable": False,
            "children": sorted(grouped[namespace], key=lambda c: c["title"]),
        }
        for namespace in sorted(grouped)
    )
    return nodes


def _pick_scope(
    picked: object,
    *,
    fallback: str,
    valid_keys: set[str],
) -> str:
    """Coerce ``st_ant_tree`` output into a real scope key.

    ``st_ant_tree`` may return a list (when ``multiple=True``) or a bare
    string; group-node placeholders (``__dir__/...``) bounce back to the
    fallback so a click on a header doesn't deselect everything.
    """
    candidate: str | None = None
    if isinstance(picked, list) and picked:
        candidate = str(picked[0])
    elif isinstance(picked, str):
        candidate = picked
    if not candidate:
        return fallback
    if candidate.startswith(_DIR_PREFIX):
        return fallback
    if candidate not in valid_keys:
        return fallback
    return candidate


def render_module_scope_selector(
    *,
    sidebar: bool = True,
    in_sidebar: bool = False,
    help: str | None = None,  # noqa: A002 — mirrors streamlit's parameter name
) -> str:
    """Return active scope key: ``all`` | ``core`` | ``<module_id>``."""

    opts = module_scope_options(REPO_ROOT)
    keys = {k for k, _ in opts}
    qp = module_scope_from_query()
    default_key = normalize_module_scope(
        qp or str(st.session_state.get(_STORAGE_KEY) or ALL_MODULES_KEY)
    )
    if default_key not in keys:
        default_key = ALL_MODULES_KEY

    tree_data = build_module_scope_tree(opts)
    help_text = (
        help
        or "All — core + every feature module. Core — repo root only. "
        "A module — only that module's scenarios, overlay rules, wiki, and areas."
    )

    def _render() -> object:
        st.caption(help_text)
        return st_ant_tree(
            treeData=tree_data,
            treeCheckable=False,
            multiple=False,
            showSearch=True,
            placeholder="Module",
            defaultValue=[default_key],
            width_dropdown="100%",
            max_height=380,
            treeLine=True,
            only_children_select=False,
            allowClear=False,
            key="module_scope_tree",
        )

    if in_sidebar:
        # Caller already opened ``with st.sidebar:`` — render in that context.
        picked = _render()
    else:
        container = st.sidebar if sidebar else st
        with container:  # ty: ignore[invalid-context-manager]
            picked = _render()

    scope = _pick_scope(picked, fallback=default_key, valid_keys=keys)
    if qp != scope:
        st.query_params[_QUERY_PARAM] = scope
        st.session_state[_STORAGE_KEY] = scope
        st.rerun()
    st.session_state[_STORAGE_KEY] = scope
    return scope
