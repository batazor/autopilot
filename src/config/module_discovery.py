"""Discover feature and core modules under ``modules/``.

Any directory that contains ``module.yaml`` is a module root (searched recursively).
Discovery order: all ``modules/core/**`` first, then other ``modules/**``, each
group sorted by relative path (case-insensitive).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

from config.paths import repo_root as default_repo_root

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CORE_MODULES_DIR = "core"
MODULE_MANIFEST = "module.yaml"
IGNORED_MODULE_DIR_NAMES = frozenset({"draft", "drafts"})


def _module_sort_key(module_dir: Path, modules_dir: Path) -> tuple[int, str]:
    rel = module_dir.relative_to(modules_dir)
    is_core = bool(rel.parts) and rel.parts[0] == CORE_MODULES_DIR
    return (0 if is_core else 1, rel.as_posix().lower())


def iter_module_dirs(repo_root: Path | None = None) -> Iterator[Path]:
    """Yield every ``modules/**/`` dir that contains ``module.yaml``."""

    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    modules_dir = root / "modules"
    if not modules_dir.is_dir():
        return

    found: list[Path] = []
    for manifest in modules_dir.rglob(MODULE_MANIFEST):
        if not manifest.is_file():
            continue
        module_dir = manifest.parent
        rel_parts = module_dir.relative_to(modules_dir).parts
        if any(
            part.startswith(".") or part.lower() in IGNORED_MODULE_DIR_NAMES
            for part in rel_parts
        ):
            continue
        found.append(module_dir)

    for module_dir in sorted(found, key=lambda p: _module_sort_key(p, modules_dir)):
        yield module_dir


def module_storage_key(module_dir: Path, repo_root: Path | None = None) -> str:
    """Stable id for logs/UI: ``core/a/b`` under ``modules/core/``, else ``a/b``."""
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    try:
        rel = module_dir.resolve().relative_to((root / "modules").resolve())
    except ValueError:
        return module_dir.name
    parts = rel.parts
    if parts and parts[0] == CORE_MODULES_DIR:
        return "/".join((CORE_MODULES_DIR, *parts[1:]))
    return "/".join(parts) if parts else module_dir.name


def is_core_nested_module(module_dir: Path, repo_root: Path | None = None) -> bool:
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    try:
        rel = module_dir.resolve().relative_to((root / "modules").resolve())
    except ValueError:
        return False
    return bool(rel.parts) and rel.parts[0] == CORE_MODULES_DIR


def load_module_yaml(module_dir: Path) -> dict[str, Any]:
    path = module_dir / MODULE_MANIFEST
    if not path.is_file():
        return {}
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def module_meta_id(module_dir: Path) -> str:
    meta = load_module_yaml(module_dir)
    return str(meta.get("id") or module_dir.name).strip() or module_dir.name


def module_scope_aliases(module_dir: Path, repo_root: Path) -> frozenset[str]:
    """Strings that may select this module in UI / path filters."""
    storage = module_storage_key(module_dir, repo_root)
    meta_id = module_meta_id(module_dir)
    aliases = {meta_id, storage, module_dir.name, storage.split("/")[-1]}
    try:
        rel = module_dir.resolve().relative_to(repo_root.resolve()).as_posix()
        aliases.add(rel)
        if rel.startswith("modules/"):
            aliases.add(rel.removeprefix("modules/"))
    except ValueError:
        pass
    return frozenset(aliases)


def module_matches_scope(module_dir: Path, scope: str, repo_root: Path) -> bool:
    """Whether ``module_dir`` belongs to wiki/overlay scope ``scope``."""
    from config.module_registry import ALL_MODULES_KEY, CORE_MODULE_KEY

    if scope == ALL_MODULES_KEY:
        return True
    if scope == CORE_MODULE_KEY:
        return is_core_nested_module(module_dir, repo_root)
    return scope in module_scope_aliases(module_dir, repo_root)


def iter_module_area_manifests(repo_root: Path) -> list[Path]:
    """Module-local area manifests in deterministic order."""
    out: list[Path] = []
    for module_dir in iter_module_dirs(repo_root):
        for name in ("area.yaml", "area.yml", "area.json"):
            manifest = module_dir / name
            if manifest.is_file():
                out.append(manifest)
                break
    return out
