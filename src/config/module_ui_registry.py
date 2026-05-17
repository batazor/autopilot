"""Discover Streamlit pages contributed by feature modules.

Declare in ``module.yaml``::

    ui:
      path: ui/page.py
      title: Gift codes
      nav: DB
      url_path: gift_codes   # optional

Or several pages::

    ui:
      - path: ui/page.py
        title: Gift codes
        nav: DB

``nav`` is the sidebar group label passed to ``st.navigation`` (e.g. ``DB``,
``Operate``, ``Wiki``, ``Debug``, ``Config``). When ``title`` is omitted the
module ``title`` from ``module.yaml`` is used. When ``url_path`` is omitted
Streamlit derives it from the file stem.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import yaml

from config.module_discovery import iter_module_dirs, module_meta_id
from config.paths import repo_root as default_repo_root

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_NAV_GROUP = "Modules"


@dataclass(frozen=True)
class ModuleUiPageSpec:
    module_id: str
    path: Path
    title: str
    nav_group: str
    url_path: str | None = None



def _load_module_yaml(module_dir: Path) -> dict[str, Any]:
    path = module_dir / "module.yaml"
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _normalize_ui_entries(
    raw_ui: object,
    *,
    module_id: str,
    default_title: str,
) -> list[dict[str, Any]]:
    if raw_ui is None:
        return []
    if isinstance(raw_ui, str):
        path = raw_ui.strip()
        if not path:
            return []
        return [{"path": path}]
    if isinstance(raw_ui, dict):
        return [cast("dict[str, Any]", raw_ui)]
    if isinstance(raw_ui, list):
        return [cast("dict[str, Any]", e) for e in raw_ui if isinstance(e, dict)]
    logger.warning("module ui: %s has invalid `ui:` — expected str, dict, or list", module_id)
    return []


def iter_module_ui_page_specs(repo_root: Path | None = None) -> list[ModuleUiPageSpec]:
    """All module UI page specs in sorted ``module_id`` order."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    specs: list[ModuleUiPageSpec] = []
    for module_dir in iter_module_dirs(root):
        meta = _load_module_yaml(module_dir)
        if not meta:
            continue
        module_id = module_meta_id(module_dir)
        default_title = str(meta.get("title") or module_id).strip() or module_id
        for entry in _normalize_ui_entries(meta.get("ui"), module_id=module_id, default_title=default_title):
            rel = str(entry.get("path") or "ui/page.py").strip() or "ui/page.py"
            page_path = (module_dir / rel).resolve()
            if not page_path.is_file():
                logger.warning(
                    "module ui: %s page missing at %s — skipping",
                    module_id,
                    page_path,
                )
                continue
            title = str(entry.get("title") or default_title).strip() or default_title
            nav_group = str(entry.get("nav") or DEFAULT_NAV_GROUP).strip() or DEFAULT_NAV_GROUP
            url_raw = entry.get("url_path")
            url_path = str(url_raw).strip() if url_raw is not None and str(url_raw).strip() else None
            specs.append(
                ModuleUiPageSpec(
                    module_id=module_id,
                    path=page_path,
                    title=title,
                    nav_group=nav_group,
                    url_path=url_path,
                )
            )
    return specs


def group_module_ui_page_specs(
    repo_root: Path | None = None,
) -> dict[str, list[ModuleUiPageSpec]]:
    """``{nav_group: [spec, ...]}`` preserving registration order per group."""
    grouped: dict[str, list[ModuleUiPageSpec]] = {}
    for spec in iter_module_ui_page_specs(repo_root):
        grouped.setdefault(spec.nav_group, []).append(spec)
    return grouped
