"""Discover feature and core modules under :func:`module_roots_for`.

Any directory that contains ``module.yaml`` is a module root (searched recursively).
Discovery order: all ``core/**`` first, then other modules, each group sorted by
relative path (case-insensitive).

Phase 4: every helper that resolves a module-tree path takes an explicit
``game`` argument. Call sites without an instance context can pass ``None`` to
get the default game, but workers and game-scoped API handlers should always
thread the active game through so Kingshot modules don't leak into WOS state.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from config.games import module_path_prefixes, module_roots_for, resolve_module_catalog
from config.paths import repo_root as default_repo_root

CORE_MODULES_DIR = "core"
MODULE_MANIFEST = "module.yaml"
IGNORED_MODULE_DIR_NAMES = frozenset({"draft", "drafts"})
CATALOG_OVERLAY_DIR_NAMES = frozenset({"beta"})


def _resolve_game(game: str | None) -> str:
    return resolve_module_catalog(game)


def _module_sort_key(entry: tuple[int, str, Path]) -> tuple[int, str, int]:
    order, rel_s, _module_dir = entry
    rel = Path(rel_s)
    is_core = bool(rel.parts) and rel.parts[0] == CORE_MODULES_DIR
    return (0 if is_core else 1, rel.as_posix().lower(), order)


def iter_module_dirs(
    repo_root: Path | None = None,
    *,
    game: str | None = None,
) -> tuple[Path, ...]:
    """Every module dir in ``game``/catalog roots that contains ``module.yaml``.

    The result is cached for the process lifetime — the rglob over the module
    tree previously dominated overlay-tick / approval-view CPU. Module layout
    is static at runtime in production; tests using ``tmp_path`` get distinct
    cache keys. Call :func:`_clear_module_discovery_caches` if you mutate the
    module tree inside one test.

    ``game`` defaults to the active module catalog. For ``wos_beta`` discovery
    walks ``games/wos`` first and then ``games/wos/beta`` as an overlay.
    """

    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    g = _resolve_game(game)
    dirs = _module_dirs_cached(g, str(root))
    if any(not (d / MODULE_MANIFEST).is_file() for d in dirs):
        # A module was deleted (or renamed) on disk while the process-lifetime
        # cache was warm — re-glob so consumers stop reading vanished paths.
        _clear_module_discovery_caches()
        dirs = _module_dirs_cached(g, str(root))
    return dirs


@lru_cache(maxsize=16)
def _module_dirs_cached(game: str, root_s: str) -> tuple[Path, ...]:
    root = Path(root_s)
    found: list[tuple[int, str, Path]] = []
    disabled_overlay_rels: set[str] = set()
    roots = module_roots_for(game, repo_root=root)

    for order, modules_dir in enumerate(roots):
        if not modules_dir.is_dir():
            continue
        for manifest in modules_dir.rglob(MODULE_MANIFEST):
            if not manifest.is_file():
                continue
            module_dir = manifest.parent
            rel = module_dir.relative_to(modules_dir)
            rel_parts = rel.parts
            if rel_parts and rel_parts[0] in CATALOG_OVERLAY_DIR_NAMES:
                continue
            if any(
                part.startswith(".") or part.lower() in IGNORED_MODULE_DIR_NAMES
                for part in rel_parts
            ):
                continue
            rel_s = rel.as_posix()
            if not _module_manifest_enabled(manifest):
                if order > 0:
                    disabled_overlay_rels.add(rel_s)
                continue
            found.append((order, rel_s, module_dir))

    visible = [
        entry
        for entry in found
        if not (entry[0] == 0 and entry[1] in disabled_overlay_rels)
    ]
    return tuple(entry[2] for entry in sorted(visible, key=_module_sort_key))


def _module_manifest_enabled(manifest: Path) -> bool:
    """`enabled: false` in module.yaml hides a module from every discovery path.

    Skeleton modules (regions not labeled yet) opt out here so the navigator,
    overlay engine, scenario loader, and startup validator all skip them in
    lockstep — partial wiring would otherwise leak as runtime errors or
    validation failures.
    """
    try:
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8"))
    except Exception:
        return True
    if not isinstance(raw, dict):
        return True
    flag = raw.get("enabled", True)
    if isinstance(flag, bool):
        return flag
    if isinstance(flag, str):
        return flag.strip().lower() not in {"false", "0", "no", "off"}
    return True


def _clear_module_discovery_caches() -> None:
    """Drop module-discovery caches (tests that mutate the module tree)."""
    _module_dirs_cached.cache_clear()
    _iter_module_area_manifests_cached.cache_clear()


def _module_rel_for_catalog(
    module_dir: Path,
    repo_root: Path,
    *,
    game: str,
) -> Path | None:
    module_resolved = module_dir.resolve()
    for modules_root in reversed(module_roots_for(game, repo_root=repo_root)):
        try:
            return module_resolved.relative_to(modules_root.resolve())
        except ValueError:
            continue
    return None


def module_storage_key(
    module_dir: Path,
    repo_root: Path | None = None,
    *,
    game: str | None = None,
) -> str:
    """Game-prefixed stable id for logs/UI/Redis.

    Returns ``"<game>:core/a/b"`` (or ``"<game>:a/b"`` for non-core modules).
    Falls back to ``module_dir.name`` when the path isn't under the game's
    modules root. Phase 4: the ``<game>:`` prefix is what lets Redis keys built
    from the storage key stay disjoint between games.
    """
    g = _resolve_game(game)
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    rel = _module_rel_for_catalog(module_dir, root, game=g)
    if rel is None:
        return module_dir.name
    parts = rel.parts
    if parts and parts[0] == CORE_MODULES_DIR:
        suffix = "/".join((CORE_MODULES_DIR, *parts[1:]))
    elif parts:
        suffix = "/".join(parts)
    else:
        return module_dir.name
    return f"{g}:{suffix}"


def is_core_nested_module(
    module_dir: Path,
    repo_root: Path | None = None,
    *,
    game: str | None = None,
) -> bool:
    g = _resolve_game(game)
    root = (repo_root if repo_root is not None else default_repo_root()).resolve()
    rel = _module_rel_for_catalog(module_dir, root, game=g)
    if rel is None:
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


def module_scope_aliases(
    module_dir: Path,
    repo_root: Path,
    *,
    game: str | None = None,
) -> frozenset[str]:
    """Strings that may select this module in UI / path filters.

    The game-prefixed storage key (``wos:core/heroes``) is included alongside
    its unprefixed forms (``core/heroes``, ``heroes``) so scope strings from
    older URLs / configs still match.
    """
    g = _resolve_game(game)
    storage = module_storage_key(module_dir, repo_root, game=g)
    storage_unprefixed = storage.removeprefix(f"{g}:")
    meta_id = module_meta_id(module_dir)
    aliases = {
        meta_id,
        storage,
        storage_unprefixed,
        module_dir.name,
        storage_unprefixed.split("/")[-1],
    }
    try:
        rel = module_dir.resolve().relative_to(repo_root.resolve()).as_posix()
        aliases.add(rel)
        for prefix_raw in module_path_prefixes(g):
            prefix = f"{prefix_raw}/"
            if rel.startswith(prefix):
                aliases.add(rel.removeprefix(prefix))
    except ValueError:
        pass
    return frozenset(aliases)


def module_matches_scope(
    module_dir: Path,
    scope: str,
    repo_root: Path,
    *,
    game: str | None = None,
) -> bool:
    """Whether ``module_dir`` belongs to wiki/overlay scope ``scope``."""
    from config.module_registry import ALL_MODULES_KEY, CORE_MODULE_KEY

    g = _resolve_game(game)
    if scope == ALL_MODULES_KEY:
        return True
    if scope == CORE_MODULE_KEY:
        return is_core_nested_module(module_dir, repo_root, game=g)
    return scope in module_scope_aliases(module_dir, repo_root, game=g)


def iter_module_area_manifests(
    repo_root: Path,
    *,
    game: str | None = None,
) -> list[Path]:
    """Module-local area manifests in deterministic order (process-cached)."""
    g = _resolve_game(game)
    root_s = str(repo_root.resolve())
    manifests = _iter_module_area_manifests_cached(g, root_s)
    if any(not m.is_file() for m in manifests):
        # Same self-heal as iter_module_dirs: a manifest vanished from disk
        # while the cache was warm — rediscover instead of serving dead paths.
        _clear_module_discovery_caches()
        manifests = _iter_module_area_manifests_cached(g, root_s)
    return list(manifests)


@lru_cache(maxsize=8)
def _iter_module_area_manifests_cached(game: str, root_s: str) -> tuple[Path, ...]:
    out: list[Path] = []
    for module_dir in iter_module_dirs(Path(root_s), game=game):
        for name in ("area.yaml", "area.yml", "area.json"):
            manifest = module_dir / name
            if manifest.is_file():
                out.append(manifest)
                break
    return tuple(out)
