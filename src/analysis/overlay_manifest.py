from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

try:
    from yaml import CSafeLoader as _YamlSafeLoader
except ImportError:
    from yaml import SafeLoader as _YamlSafeLoader  # type: ignore[assignment]

from analysis.overlay_compile import CompiledOverlayPlan, compile_overlay_plan


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    raw = yaml.load(path.read_text(encoding="utf-8"), Loader=_YamlSafeLoader)
    return raw if isinstance(raw, dict) else {}


def _resolve_includes(manifest_path: Path, include: list[object]) -> list[Path]:
    out: list[Path] = []
    for item in include:
        s = str(item or "").strip()
        if not s:
            continue
        p = Path(s)
        if not p.is_absolute():
            p = manifest_path.parent / p
        out.append(p)
    return out


def load_analyze_yaml(path: Path) -> dict[str, Any]:
    """Load one module ``analyze/analyze.yaml`` (supports legacy ``include:`` lists)."""
    if not path.is_file():
        return {}

    raw = _load_yaml_dict(path)

    overlay_merged: list[dict[str, Any]] = []
    ov = raw.get("overlay")
    if isinstance(ov, list):
        overlay_merged.extend([r for r in ov if isinstance(r, dict)])

    inc = raw.get("include")
    if isinstance(inc, list) and inc:
        for inc_path in _resolve_includes(path, inc):
            if not inc_path.is_file():
                continue
            doc = _load_yaml_dict(inc_path)
            for k, v in doc.items():
                if k == "overlay":
                    continue
                if k not in raw:
                    raw[k] = v
            ov2 = doc.get("overlay")
            if isinstance(ov2, list):
                overlay_merged.extend([r for r in ov2 if isinstance(r, dict)])

    if overlay_merged:
        raw["overlay"] = overlay_merged
    return raw


def iter_analyze_manifest_paths(
    repo_root: Path,
    module_scope: str | None = None,
) -> list[Path]:
    """Module overlay manifests in discovery order."""
    from dsl.registry import iter_module_analyze_manifests

    return iter_module_analyze_manifests(repo_root, module_scope)


def _stat_fingerprint(path: Path) -> tuple[str, int, int] | None:
    """``(resolved path, size, mtime_ns)`` for cache invalidation; ``None`` if missing."""
    try:
        if not path.is_file():
            return None
        st = path.stat()
        return (str(path.resolve()), st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _collect_analyze_files(manifest_path: Path, seen: set[Path] | None = None) -> list[Path]:
    """Manifest plus every ``include:`` target (recursive), deduped in walk order."""
    if seen is None:
        seen = set()
    resolved = manifest_path.resolve()
    if resolved in seen or not manifest_path.is_file():
        return []
    seen.add(resolved)
    out: list[Path] = [manifest_path]
    raw = _load_yaml_dict(manifest_path)
    inc = raw.get("include")
    if isinstance(inc, list) and inc:
        for inc_path in _resolve_includes(manifest_path, inc):
            out.extend(_collect_analyze_files(inc_path, seen))
    return out


# Cache of (manifest_resolved_str, mtime_ns, size) -> tuple of resolved include path strings.
# Keyed on the manifest's own stat so a file change invalidates only its entry; the YAML
# parse needed to discover include: targets is skipped while the manifest is untouched.
_INCLUDE_WALK_CACHE: dict[tuple[str, int, int], tuple[str, ...]] = {}


def _walk_include_paths(manifest_path: Path) -> tuple[str, ...]:
    """Resolved manifest + include: tree as strings; cached by (mtime, size)."""
    try:
        if not manifest_path.is_file():
            return ()
        st = manifest_path.stat()
    except OSError:
        return ()
    key = (str(manifest_path.resolve()), st.st_mtime_ns, st.st_size)
    cached = _INCLUDE_WALK_CACHE.get(key)
    if cached is not None:
        return cached
    files = _collect_analyze_files(manifest_path)
    resolved = tuple(str(p.resolve()) for p in files)
    _INCLUDE_WALK_CACHE[key] = resolved
    return resolved


def analyze_manifests_fingerprint(
    repo_root: Path,
    module_scope: str | None = None,
) -> tuple[tuple[str, int, int], ...]:
    """Sorted ``(path, size, mtime_ns)`` for every manifest and ``include:`` file."""
    entries: list[tuple[str, int, int]] = []
    seen_paths: set[str] = set()
    for manifest in iter_analyze_manifest_paths(repo_root, module_scope):
        for resolved_str in _walk_include_paths(manifest):
            if resolved_str in seen_paths:
                continue
            seen_paths.add(resolved_str)
            fp = _stat_fingerprint(Path(resolved_str))
            if fp is not None:
                entries.append(fp)
    entries.sort(key=lambda row: row[0])
    return tuple(entries)


def analyze_manifests_mtime(
    repo_root: Path,
    module_scope: str | None = None,
) -> float | None:
    """Latest ``st_mtime`` among manifests and ``include:`` targets (legacy float bucket)."""
    mt: float | None = None
    for _path, _size, mtime_ns in analyze_manifests_fingerprint(repo_root, module_scope):
        m = mtime_ns / 1_000_000_000
        mt = m if mt is None else max(mt, m)
    return mt


def clear_merged_analyze_yaml_cache() -> None:
    """Drop cached merged manifests (tests, hot reload)."""
    _load_merged_analyze_yaml_cached.cache_clear()
    _compiled_overlay_plan_cached.cache_clear()
    _INCLUDE_WALK_CACHE.clear()


def compiled_overlay_plan(
    repo_root: Path,
    *,
    module_scope: str | None = None,
    device_level_only: bool = False,
) -> CompiledOverlayPlan:
    """Compiled overlay rules for ``run_overlay_analysis`` (mtime-keyed cache)."""
    root = repo_root.resolve()
    fp = analyze_manifests_fingerprint(root, module_scope)
    return _compiled_overlay_plan_cached(
        str(root),
        module_scope or "",
        fp,
        device_level_only,
    )


@lru_cache(maxsize=64)
def _compiled_overlay_plan_cached(
    repo_root_s: str,
    module_scope: str,
    manifests_fp: tuple[tuple[str, int, int], ...],
    device_level_only: bool,
) -> CompiledOverlayPlan:
    _ = manifests_fp
    merged = _load_merged_analyze_yaml_cached(
        repo_root_s, module_scope, manifests_fp
    )
    overlay = merged.get("overlay")
    rules = overlay if isinstance(overlay, list) else []
    if device_level_only:
        rules = [
            rule
            for rule in rules
            if isinstance(rule, dict) and rule.get("device_level") is True
        ]
    return compile_overlay_plan(rules)


def load_merged_analyze_yaml(
    repo_root: Path,
    *,
    module_scope: str | None = None,
) -> dict[str, Any]:
    """Merge ``overlay`` rules from every ``modules/core/*/analyze`` + feature module."""
    root = repo_root.resolve()
    fp = analyze_manifests_fingerprint(root, module_scope)
    return _load_merged_analyze_yaml_cached(
        str(root),
        module_scope or "",
        fp,
    )


@lru_cache(maxsize=64)
def _load_merged_analyze_yaml_cached(
    repo_root_s: str,
    module_scope: str,
    manifests_fp: tuple[tuple[str, int, int], ...],
) -> dict[str, Any]:
    # manifests_fp is part of the cache key; edits invalidate automatically.
    _ = manifests_fp
    repo_root = Path(repo_root_s)
    scope = module_scope or None
    merged: dict[str, Any] = {}
    overlay: list[dict[str, Any]] = []

    for module_manifest in iter_analyze_manifest_paths(repo_root, scope):
        doc = load_analyze_yaml(module_manifest)
        module_overlay = doc.get("overlay")
        if isinstance(module_overlay, list):
            overlay.extend(r for r in module_overlay if isinstance(r, dict))

    merged["overlay"] = overlay
    return merged
