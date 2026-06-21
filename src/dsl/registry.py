"""Module DSL source registry.

Runnable YAML files live in each module's ``scenarios/`` tree under
``modules/`` (``modules/core/<id>/`` for core). Discovery and loading are
implemented here; execution uses ``tasks.dsl_scenario``.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from config.module_discovery import (
    is_core_nested_module,
    iter_module_dirs,
    module_matches_scope,
    module_meta_id,
)
from config.module_registry import ALL_MODULES_KEY, CORE_MODULE_KEY, normalize_module_scope


@dataclass(frozen=True)
class ScenarioRoot:
    """One directory that can contain scenario YAML files."""

    path: Path
    label: str
    module_id: str | None = None


def scenario_roots(
    repo_root: Path,
    module_scope: str | None = None,
    *,
    game: str | None = None,
) -> tuple[ScenarioRoot, ...]:
    """Scenario roots in deterministic lookup order, optionally scoped.

    Cached for the process lifetime — this used to be called on every
    ``template_resolver.resolve`` (i.e. every approval-view render) and
    triggered a full ``modules/**`` walk via :func:`iter_module_dirs`.
    """
    from config.games import resolve_module_catalog

    scope = normalize_module_scope(module_scope)
    g = resolve_module_catalog(game)
    return _scenario_roots_cached(str(repo_root.resolve()), scope, g)


@lru_cache(maxsize=64)
def _scenario_roots_cached(
    root_s: str, scope: str, game: str
) -> tuple[ScenarioRoot, ...]:
    repo_root = Path(root_s)
    roots: list[ScenarioRoot] = []

    for module_dir in iter_module_dirs(repo_root, game=game):
        if scope == CORE_MODULE_KEY and not is_core_nested_module(
            module_dir, repo_root, game=game
        ):
            continue
        if scope not in (ALL_MODULES_KEY, CORE_MODULE_KEY) and not module_matches_scope(
            module_dir, scope, repo_root, game=game
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
    return tuple(roots)


def _clear_scenario_root_caches() -> None:
    """Drop the scenario-roots cache (tests that mutate the module tree)."""
    _scenario_roots_cached.cache_clear()
    _scenario_roots_for_label.cache_clear()


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
    *,
    game: str | None = None,
) -> list[tuple[ScenarioRoot, Path]]:
    """All scenario YAMLs from core and modules, excluding drafts."""
    out: list[tuple[ScenarioRoot, Path]] = []
    for root in scenario_roots(repo_root, module_scope, game=game):
        for path in root.path.rglob("*.yaml"):
            if is_under_drafts(path, root.path):
                continue
            out.append((root, path))
    return sorted(out, key=lambda item: (item[0].label, item[1].as_posix()))


def scenario_yaml_tree_fingerprint(
    repo_root: Path,
    *,
    game: str | None = None,
) -> tuple[str, tuple[tuple[str, int, int], ...]]:
    """Stable (mtime, size) fingerprint for every runnable scenario YAML (core + modules)."""

    root = repo_root.resolve()
    items: list[tuple[str, int, int]] = []
    for _sr, path in iter_scenario_yaml_files(root, game=game):
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
    *,
    game: str | None = None,
) -> list[Path]:
    """Module-local analyze manifests in deterministic order.

    When ``module_scope`` selects a specific module, infrastructure modules
    listed in :data:`config.test_module.INFRASTRUCTURE_MODULE_IDS` (e.g.
    ``core/popup``) are still included — without them the game gets stuck on
    unknown popups during test-module runs.
    """
    from config.test_module import INFRASTRUCTURE_MODULE_IDS

    scope = normalize_module_scope(module_scope)
    out: list[Path] = []
    specific_scope = scope not in (ALL_MODULES_KEY, CORE_MODULE_KEY)
    for module_dir in iter_module_dirs(repo_root, game=game):
        if scope == CORE_MODULE_KEY and not is_core_nested_module(
            module_dir, repo_root, game=game
        ):
            continue
        if (
            specific_scope
            and not module_matches_scope(module_dir, scope, repo_root, game=game)
            and module_meta_id(module_dir) not in INFRASTRUCTURE_MODULE_IDS
        ):
            continue
        manifest = module_dir / "analyze" / "analyze.yaml"
        if manifest.is_file():
            out.append(manifest)
    return out


@lru_cache(maxsize=16)
def _scenario_roots_for_label(
    repo_root_s: str, game: str
) -> tuple[tuple[str, str, str | None], ...]:
    """Cached ``scenario_roots`` rows as strings (``Path`` is not hashable)."""
    roots = scenario_roots(Path(repo_root_s), ALL_MODULES_KEY, game=game)
    return tuple((str(r.path), r.label, r.module_id) for r in roots)


def scenario_source_label(
    path: Path,
    repo_root: Path,
    *,
    game: str | None = None,
) -> str:
    """Human-readable source label for logs/UI, relative to its scenario root."""
    from config.games import default_game

    g = (game or default_game()).strip()
    for root_path, label, module_id in _scenario_roots_for_label(str(repo_root), g):
        try:
            rel = path.relative_to(root_path).as_posix()
        except ValueError:
            continue
        if module_id is None:
            return rel
        return f"{label}/{rel}"
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()
