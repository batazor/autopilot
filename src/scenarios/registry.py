"""Scenario source registry.

Core scenarios live under ``scenarios/``. Optional feature modules can add
their own runnable YAMLs under ``modules/<module_id>/scenarios/`` without
being copied into the core tree. Base modules live under ``modules/core/<id>/``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from config.module_discovery import (
    is_core_nested_module,
    iter_module_dirs,
    module_matches_scope,
    module_meta_id,
)
from config.module_registry import ALL_MODULES_KEY, CORE_MODULE_KEY, normalize_module_scope
from config.paths import core_scenarios_root


@dataclass(frozen=True)
class ScenarioRoot:
    """One directory that can contain scenario YAML files."""

    path: Path
    label: str
    module_id: str | None = None


def scenario_roots(
    repo_root: Path,
    module_scope: str | None = None,
) -> list[ScenarioRoot]:
    """Return scenario roots in deterministic lookup order, optionally filtered."""
    scope = normalize_module_scope(module_scope)
    roots: list[ScenarioRoot] = []
    core = core_scenarios_root(repo_root)
    if core.is_dir() and scope in (ALL_MODULES_KEY, CORE_MODULE_KEY):
        roots.append(ScenarioRoot(path=core, label="scenarios"))

    for module_dir in iter_module_dirs(repo_root):
        if scope == CORE_MODULE_KEY and not is_core_nested_module(module_dir, repo_root):
            continue
        if scope not in (ALL_MODULES_KEY, CORE_MODULE_KEY) and not module_matches_scope(
            module_dir, scope, repo_root
        ):
            continue
        scen_dir = module_dir / "scenarios"
        if scen_dir.is_dir():
            label = scen_dir.relative_to(repo_root).as_posix()
            roots.append(
                ScenarioRoot(
                    path=scen_dir,
                    label=label,
                    module_id=module_meta_id(module_dir),
                )
            )
    return roots


def is_under_drafts(path: Path, root: Path) -> bool:
    """True when ``path`` is below a ``drafts`` directory inside ``root``."""
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return any(part.lower() == "drafts" for part in rel.parts)


def iter_scenario_yaml_files(
    repo_root: Path,
    module_scope: str | None = None,
) -> list[tuple[ScenarioRoot, Path]]:
    """All scenario YAMLs from core and modules, excluding drafts."""
    out: list[tuple[ScenarioRoot, Path]] = []
    for root in scenario_roots(repo_root, module_scope):
        for path in root.path.rglob("*.yaml"):
            if is_under_drafts(path, root.path):
                continue
            out.append((root, path))
    return sorted(out, key=lambda item: (item[0].label, item[1].as_posix()))


def scenario_yaml_tree_fingerprint(repo_root: Path) -> tuple[str, tuple[tuple[str, int, int], ...]]:
    """Stable (mtime, size) fingerprint for every runnable scenario YAML (core + modules)."""

    root = repo_root.resolve()
    items: list[tuple[str, int, int]] = []
    for _sr, path in iter_scenario_yaml_files(root):
        try:
            st = path.stat()
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        items.append((rel, int(st.st_mtime_ns), int(st.st_size)))
    items.sort(key=lambda x: x[0])
    return (str(root), tuple(items))


def iter_module_analyze_manifests(
    repo_root: Path,
    module_scope: str | None = None,
) -> list[Path]:
    """Module-local analyze manifests in deterministic order."""
    scope = normalize_module_scope(module_scope)
    out: list[Path] = []
    for module_dir in iter_module_dirs(repo_root):
        if scope == CORE_MODULE_KEY and not is_core_nested_module(module_dir, repo_root):
            continue
        if scope not in (ALL_MODULES_KEY, CORE_MODULE_KEY) and not module_matches_scope(
            module_dir, scope, repo_root
        ):
            continue
        manifest = module_dir / "analyze" / "analyze.yaml"
        if manifest.is_file():
            out.append(manifest)
    return out


def scenario_source_label(path: Path, repo_root: Path) -> str:
    """Human-readable source label for logs/UI, relative to its scenario root."""
    for root in scenario_roots(repo_root, ALL_MODULES_KEY):
        try:
            rel = path.relative_to(root.path).as_posix()
        except ValueError:
            continue
        if root.module_id is None:
            return rel
        return f"{root.label}/{rel}"
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()
