from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_yaml_dict(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
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
    from scenarios.registry import iter_module_analyze_manifests

    return iter_module_analyze_manifests(repo_root, module_scope)


def analyze_manifests_mtime(
    repo_root: Path,
    module_scope: str | None = None,
) -> float | None:
    """Latest mtime among module ``analyze/analyze.yaml`` files (cache invalidation)."""
    mt: float | None = None
    for path in iter_analyze_manifest_paths(repo_root, module_scope):
        try:
            if not path.is_file():
                continue
            m = path.stat().st_mtime
            mt = m if mt is None else max(mt, m)
        except OSError:
            continue
    return mt


def load_merged_analyze_yaml(
    repo_root: Path,
    *,
    module_scope: str | None = None,
) -> dict[str, Any]:
    """Merge ``overlay`` rules from every ``modules/core/*/analyze`` + feature module."""
    merged: dict[str, Any] = {}
    overlay: list[dict[str, Any]] = []

    for module_manifest in iter_analyze_manifest_paths(repo_root, module_scope):
        doc = load_analyze_yaml(module_manifest)
        module_overlay = doc.get("overlay")
        if isinstance(module_overlay, list):
            overlay.extend(r for r in module_overlay if isinstance(r, dict))

    merged["overlay"] = overlay
    return merged
