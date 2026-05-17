"""Build ``st.Page`` objects from module ``module.yaml`` UI declarations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from config.module_ui_registry import ModuleUiPageSpec, group_module_ui_page_specs


def streamlit_page_from_spec(spec: ModuleUiPageSpec) -> st.Page:
    kwargs: dict[str, Any] = {"title": spec.title}
    if spec.url_path:
        kwargs["url_path"] = spec.url_path
    return st.Page(str(spec.path), **kwargs)


def module_streamlit_pages_by_nav(repo_root: Path) -> dict[str, list[st.Page]]:
    """``{nav_group: [st.Page, ...]}`` for all registered module UI pages."""

    grouped: dict[str, list[st.Page]] = {}
    for nav_group, specs in group_module_ui_page_specs(repo_root).items():
        grouped[nav_group] = [streamlit_page_from_spec(s) for s in specs]
    return grouped


def extend_nav_pages(
    nav: dict[str, list[st.Page]],
    module_pages: dict[str, list[st.Page]],
) -> dict[str, list[st.Page]]:
    """Append module pages to existing navigation groups (module pages last)."""
    out = {k: list(v) for k, v in nav.items()}
    for group, pages in module_pages.items():
        out.setdefault(group, [])
        out[group].extend(pages)
    return out
