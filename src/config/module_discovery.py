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

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from config.games import module_path_prefixes, module_roots_for, resolve_module_catalog
from config.paths import repo_root as default_repo_root

CORE_MODULES_DIR = "core"
MODULE_MANIFEST = "module.yaml"
IGNORED_MODULE_DIR_NAMES = frozenset({"draft", "drafts"})
# Overlay subtree names under a base game root (``games/wos/<name>``). Excluded
# from base-game discovery so they only load for their own catalog: ``beta`` for
# ``wos_beta``, ``ru`` for ``wos_ru`` («Белая мгла»). Keep in sync with the
# overlay leaves in ``config.games.MODULE_CATALOG_OVERLAYS``.
CATALOG_OVERLAY_DIR_NAMES = frozenset({"beta", "ru"})



@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """One parsed ``module.yaml``.

    The file used to be re-parsed by four independent loaders plus a fifth pass
    that opened it again just to read ``enabled``, and its fields were then
    passed around as raw dicts. That is why ``area:`` is honoured by the wiki
    editor and ignored by discovery, and why two manifests can point at a path
    that has not existed since Phase 3 without anything noticing.

    ``raw`` keeps whatever is not modelled so nothing is silently dropped;
    :data:`KNOWN_MANIFEST_KEYS` is what startup validation checks against.
    """

    module_dir: Path
    id: str
    title: str
    description: str
    enabled: bool
    wiki: bool
    wiki_url: str
    references: str
    scenarios: str
    area: str
    analyze: str
    exec_path: str
    routes: str
    icon: str
    default_ref: str
    capture_interval_ms: int | None
    raw: tuple[tuple[str, Any], ...]

    @property
    def scenarios_dir(self) -> Path:
        """Directory holding this module's scenario YAMLs."""
        return self.module_dir / (self.scenarios or "scenarios")


# Every key any consumer reads, plus the two nothing reads yet. `icon` is
# declared by 85 manifests and `routes` by 3 with no reader at all — kept
# listed so the unknown-key check does not flag them as typos, and so their
# deadness is visible in one place rather than inferred.
KNOWN_MANIFEST_KEYS = frozenset(
    {
        "id", "title", "description", "enabled", "wiki", "wiki_url",
        "references", "scenarios", "area", "analyze", "exec", "routes",
        "icon", "default_ref", "capture_interval_ms",
    }
)


def _manifest_bool(value: Any, *, default: bool) -> bool:
    """YAML bools, plus the string forms an operator might hand-write."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"false", "0", "no", "off"}
    return default


def load_manifest(module_dir: Path) -> ModuleManifest:
    """Parsed manifest for ``module_dir``; defaults when the file is absent."""
    path = module_dir / MODULE_MANIFEST
    stat_key: tuple[float, int] = (0.0, 0)
    try:
        st = path.stat()
        stat_key = (st.st_mtime_ns, st.st_size)
    except OSError:
        pass
    return _load_manifest_cached(str(module_dir), stat_key)


@lru_cache(maxsize=512)
def _load_manifest_cached(
    module_dir_s: str, _stat_key: tuple[float, int]
) -> ModuleManifest:
    # ``_stat_key`` is only a cache key — an edit changes it and invalidates.
    module_dir = Path(module_dir_s)
    raw: dict[str, Any] = {}
    path = module_dir / MODULE_MANIFEST
    if path.is_file():
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception:
            parsed = None
        if isinstance(parsed, dict):
            raw = parsed

    def _s(key: str) -> str:
        return str(raw.get(key) or "").strip()

    interval: int | None
    try:
        interval = int(raw["capture_interval_ms"])
    except (KeyError, TypeError, ValueError):
        interval = None

    return ModuleManifest(
        module_dir=module_dir,
        id=_s("id") or module_dir.name,
        title=_s("title"),
        description=_s("description"),
        # A malformed or missing manifest must not hide a module — that would
        # turn a YAML typo into a silently absent feature.
        enabled=_manifest_bool(raw.get("enabled", True), default=True),
        wiki=_manifest_bool(raw.get("wiki", True), default=True),
        wiki_url=_s("wiki_url"),
        references=_s("references"),
        scenarios=_s("scenarios"),
        area=_s("area"),
        analyze=_s("analyze"),
        exec_path=_s("exec"),
        routes=_s("routes"),
        icon=_s("icon"),
        default_ref=_s("default_ref"),
        capture_interval_ms=interval if interval and interval > 0 else None,
        raw=tuple(sorted((k, v) for k, v in raw.items() if isinstance(k, str))),
    )


def clear_manifest_cache() -> None:
    """Drop parsed manifests (registered in ``config.cache_registry``)."""
    _load_manifest_cached.cache_clear()


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
            if not _module_allowed(rel_s):
                # Operator allowlist (WOS_MODULES) — a hard filter ON TOP of the
                # per-module ``enabled`` flag, for temporarily running a slice
                # of the fleet (e.g. intel+arena only) without editing every
                # module.yaml. Excluded overlay modules are also stripped from
                # the base layer, same as ``enabled: false``.
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


@lru_cache(maxsize=1)
def _module_allowlist() -> frozenset[str] | None:
    """Parse ``WOS_MODULES`` into a normalized rel-path allowlist.

    ``None`` (env unset/empty) means "all modules" — the default. Otherwise a
    module is kept when its rel path matches an entry by full rel (``core/arena``),
    by basename (``arena``), or under a ``core/`` prefix (``arena`` ↔ ``core/arena``).
    Entries and rels are lower-cased and slash-normalized so operator input is
    forgiving.
    """
    raw = os.environ.get("WOS_MODULES", "").strip()
    if not raw:
        return None
    entries = {
        e.strip().replace("\\", "/").strip("/").lower()
        for e in raw.split(",")
        if e.strip()
    }
    return frozenset(entries) or None


def _module_allowed(rel_s: str) -> bool:
    allow = _module_allowlist()
    if allow is None:
        return True
    rel = rel_s.replace("\\", "/").strip("/").lower()
    base = rel.rsplit("/", 1)[-1]
    core_stripped = rel.removeprefix("core/")
    return bool({rel, base, core_stripped} & allow)


def _module_manifest_enabled(manifest: Path) -> bool:
    """`enabled: false` in module.yaml hides a module from every discovery path.

    Skeleton modules (regions not labeled yet) opt out here so the navigator,
    overlay engine, scenario loader, and startup validator all skip them in
    lockstep — partial wiring would otherwise leak as runtime errors or
    validation failures.

    Reads the shared parsed manifest: this used to open and parse the file a
    second time purely for this one flag.
    """
    return load_manifest(manifest.parent).enabled


def _clear_module_discovery_caches() -> None:
    """Drop module-discovery caches (tests that mutate the module tree)."""
    _module_dirs_cached.cache_clear()
    _load_manifest_cached.cache_clear()
    _iter_module_area_manifests_cached.cache_clear()
    _module_allowlist.cache_clear()


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
    """Raw manifest mapping.

    Kept for callers that want the untyped shape; it now projects from the one
    cached parse rather than re-reading the file. Prefer :func:`load_manifest`.
    """
    return dict(load_manifest(module_dir).raw)


def module_meta_id(module_dir: Path) -> str:
    return load_manifest(module_dir).id


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
