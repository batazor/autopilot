"""Merged wiki entry sources: core ``db/<entity>/`` + ``modules/*/wiki/<entity>/``.

The wiki UI (:mod:`ui.views.wiki_db`) used to read core ``db/<entity>/index.yaml``
exclusively. Modules can now contribute their own entries by mirroring the same
layout under ``modules/<id>/wiki/<entity>/``:

* ``modules/<id>/wiki/heroes/index.yaml`` — ``{heroes: [{id, name, wiki_url?, file?}]}``
* ``modules/<id>/wiki/heroes/<hero_id>.yaml`` — per-entity payload (same schema as core)
* ``modules/<id>/wiki/heroes/assets/<hero_id>/icon.png`` — optional tile icon

Module entries with an ``id`` matching a core entry override the core copy; new
``id`` values are appended. Every returned entry carries provenance so the UI
can show an "owned by module" badge.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

import yaml

from config.module_discovery import is_core_nested_module, iter_module_dirs, module_meta_id
from config.module_registry import ALL_MODULES_KEY, CORE_MODULE_KEY, normalize_module_scope
from config.paths import repo_root as default_repo_root

if TYPE_CHECKING:
    from pathlib import Path

CORE_SOURCE = "core"
EntityKey = Literal["buildings", "heroes", "items"]

# index.yaml has ``{<entity_key>: [...]}`` with the same plural key as the entity.
_INDEX_LIST_KEY: dict[str, str] = {
    "buildings": "buildings",
    "heroes": "heroes",
    "items": "items",
}

_ICON_EXTS = (".png", ".webp", ".jpg", ".jpeg", ".gif")


@dataclass(frozen=True)
class WikiEntry:
    """One row of a wiki index, annotated with where to find its YAML/icon."""

    entry: dict[str, Any]
    """Raw index row (``{id, name, wiki_url?, file?, ...}``)."""

    source: str
    """``"core"`` for ``db/<entity>/`` or the module id for ``modules/<id>/wiki/<entity>/``."""

    yaml_path: Path
    """Resolved path of the per-entity YAML (may not exist yet)."""

    icon_path: Path | None
    """Resolved icon path if a local image was found, else ``None``."""

    @property
    def id(self) -> str:
        return str(self.entry.get("id") or "").strip()

    @property
    def name(self) -> str:
        return str(self.entry.get("name") or "").strip()

    @property
    def is_core(self) -> bool:
        return self.source == CORE_SOURCE



def _load_yaml_dict(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return {}
    except yaml.YAMLError:
        return {}
    return raw if isinstance(raw, dict) else {}


def _index_rows(index_path: Path, list_key: str) -> list[dict[str, Any]]:
    doc = _load_yaml_dict(index_path)
    raw = doc.get(list_key)
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _entity_yaml_path(entity_dir: Path, row: dict[str, Any]) -> Path:
    file_rel = str(row.get("file") or "").strip()
    if not file_rel:
        eid = str(row.get("id") or "").strip()
        file_rel = f"{eid}.yaml" if eid else ""
    return (entity_dir / file_rel).resolve() if file_rel else entity_dir


def _core_icon_path(entity: EntityKey, entity_id: str, repo_root: Path) -> Path | None:
    base = repo_root / "db" / "assets" / "wiki" / entity / entity_id
    return _pick_icon(base)


def _module_icon_path(module_wiki_entity_dir: Path, entity_id: str) -> Path | None:
    base = module_wiki_entity_dir / "assets" / entity_id
    return _pick_icon(base)


def _pick_icon(base: Path) -> Path | None:
    if not base.is_dir():
        return None
    files = [p for p in base.iterdir() if p.is_file() and p.suffix.lower() in _ICON_EXTS]
    if not files:
        return None
    files.sort(key=lambda p: (p.suffix.lower(), p.name.lower()))
    return files[0]


def _append_module_wiki_entries(
    *,
    by_id: dict[str, WikiEntry],
    order: list[str],
    module_id: str,
    wiki_entity_dir: Path,
    list_key: str,
    entity: EntityKey,
    repo_root: Path,
) -> None:
    for row in _index_rows(wiki_entity_dir / "index.yaml", list_key):
        eid = str(row.get("id") or "").strip()
        if not eid:
            continue
        yaml_path = _entity_yaml_path(wiki_entity_dir, row)
        icon = _module_icon_path(wiki_entity_dir, eid) or _core_icon_path(entity, eid, repo_root)
        entry = WikiEntry(
            entry=dict(row),
            source=module_id,
            yaml_path=yaml_path,
            icon_path=icon,
        )
        if eid not in by_id:
            order.append(eid)
        by_id[eid] = entry


def _iter_module_wiki_dirs(repo_root: Path, entity: EntityKey) -> list[tuple[str, Path]]:
    """``[(module_id, .../wiki/<entity>/), ...]`` in discovery order."""
    out: list[tuple[str, Path]] = []
    for module_dir in iter_module_dirs(repo_root):
        wiki_entity_dir = module_dir / "wiki" / entity
        if not wiki_entity_dir.is_dir():
            continue
        out.append((module_meta_id(module_dir), wiki_entity_dir))
    return out


def load_merged_entries(
    entity: EntityKey,
    *,
    repo_root: Path | None = None,
    module_scope: str | None = None,
) -> list[WikiEntry]:
    """Core entries first, then per-module additions; module overrides take precedence.

    Order within each source is preserved from the on-disk ``index.yaml`` so the UI
    can keep the existing alphabetical layout the sync scripts produced.
    """
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    scope = normalize_module_scope(module_scope)
    list_key = _INDEX_LIST_KEY[entity]
    core_dir = root / "db" / entity

    by_id: dict[str, WikiEntry] = {}
    order: list[str] = []

    if scope in (ALL_MODULES_KEY, CORE_MODULE_KEY):
        for row in _index_rows(core_dir / "index.yaml", list_key):
            eid = str(row.get("id") or "").strip()
            if not eid:
                continue
            yaml_path = _entity_yaml_path(core_dir, row)
            icon = _core_icon_path(entity, eid, root)
            entry = WikiEntry(
                entry=dict(row),
                source=CORE_SOURCE,
                yaml_path=yaml_path,
                icon_path=icon,
            )
            if eid not in by_id:
                order.append(eid)
            by_id[eid] = entry

    if scope == CORE_MODULE_KEY:
        for module_dir in iter_module_dirs(root):
            if not is_core_nested_module(module_dir, root):
                continue
            wiki_entity_dir = module_dir / "wiki" / entity
            if not wiki_entity_dir.is_dir():
                continue
            _append_module_wiki_entries(
                by_id=by_id,
                order=order,
                module_id=module_meta_id(module_dir),
                wiki_entity_dir=wiki_entity_dir,
                list_key=list_key,
                entity=entity,
                repo_root=root,
            )
        return [by_id[eid] for eid in order]

    for module_id, wiki_entity_dir in _iter_module_wiki_dirs(root, entity):
        if scope != ALL_MODULES_KEY and module_id != scope:
            continue
        _append_module_wiki_entries(
            by_id=by_id,
            order=order,
            module_id=module_id,
            wiki_entity_dir=wiki_entity_dir,
            list_key=list_key,
            entity=entity,
            repo_root=root,
        )

    return [by_id[eid] for eid in order]


def find_entry(
    entity: EntityKey,
    entity_id: str,
    *,
    repo_root: Path | None = None,
) -> WikiEntry | None:
    """Convenience lookup by id across merged sources."""
    target = (entity_id or "").strip()
    if not target:
        return None
    for entry in load_merged_entries(entity, repo_root=repo_root):
        if entry.id == target:
            return entry
    return None
