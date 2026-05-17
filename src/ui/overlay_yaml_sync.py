"""Overlay YAML helpers for labeling-time region metadata cleanup."""
from __future__ import annotations

from pathlib import Path

import yaml

from analysis.overlay_manifest import iter_analyze_manifest_paths


def overlay_search_region_name(primary: str) -> str:
    return f"{str(primary).strip()}_search"


def overlay_tap_region_name(primary: str) -> str:
    """Auxiliary click ROI for ``primary`` overlay region (offset target for taps)."""
    return f"{str(primary).strip()}_tap"


def cascade_aux_region_names(
    primary_name: str,
    existing_names: set[str] | frozenset[str],
) -> list[str]:
    """Return the overlay aux region names that should be cascade-deleted along
    with ``primary_name``.

    - If ``primary_name`` is itself an aux region (``*_search`` / ``*_tap``),
      no cascade is performed (returns an empty list); deleting an aux must not
      take its primary down with it.
    - Only names that actually appear in ``existing_names`` are returned, so we
      never list nonexistent helpers in the confirmation prompt.
    """

    name = (primary_name or "").strip()
    if not name:
        return []
    if name.endswith(("_search", "_tap")):
        return []
    out: list[str] = []
    for aux in (overlay_tap_region_name(name),):
        if aux in existing_names:
            out.append(aux)
    return out


def _load_yaml_dict(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _iter_analyze_sources(repo_root: Path) -> list[Path]:
    """Every module ``analyze/analyze.yaml`` (and legacy ``include:`` targets)."""
    out: list[Path] = []
    for manifest in iter_analyze_manifest_paths(repo_root):
        if not manifest.is_file():
            continue
        out.append(manifest)
        raw = _load_yaml_dict(manifest)
        inc = raw.get("include")
        if isinstance(inc, list) and inc:
            for item in inc:
                s = str(item or "").strip()
                if not s:
                    continue
                p = Path(s)
                if not p.is_absolute():
                    p = manifest.parent / p
                out.append(p)
    return out


def sync_findicon_overlay_aux_keys(
    repo_root: Path,
    primary_region: str,
    *,
    use_search: bool,
) -> bool:
    """Remove obsolete explicit ``search_region`` from a matching ``findIcon`` rule."""
    _ = use_search
    primary = str(primary_region or "").strip()
    if not primary:
        return False
    any_match = False
    for path in _iter_analyze_sources(repo_root):
        if not path.is_file():
            continue
        raw = _load_yaml_dict(path)
        overlay = raw.get("overlay")
        if not isinstance(overlay, list):
            continue
        changed_rule = False
        for rule in overlay:
            if not isinstance(rule, dict):
                continue
            if str(rule.get("region") or "").strip() != primary:
                continue
            if str(rule.get("action") or "").strip() != "findIcon":
                continue
            any_match = True
            if "search_region" in rule:
                rule.pop("search_region", None)
                changed_rule = True
            break
        if not changed_rule:
            continue
        path.write_text(
            yaml.dump(
                raw,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=100,
            ),
            encoding="utf-8",
        )
    return any_match


def rename_findicon_overlay_primary(
    repo_root: Path,
    old_primary: str,
    new_primary: str,
) -> bool:
    """Sync ``analyze.yaml`` after a primary region rename in ``area.json``.

    Sets matching ``findIcon`` rule ``region`` to ``new_primary``. Drops explicit
    ``search_region`` when it equals ``{old}_search`` (runtime uses ``{new}_search``).
    """
    old_primary = str(old_primary or "").strip()
    new_primary = str(new_primary or "").strip()
    if not old_primary or not new_primary or old_primary == new_primary:
        return False
    sn_old = overlay_search_region_name(old_primary)
    wrote = False
    for path in _iter_analyze_sources(repo_root):
        if not path.is_file():
            continue
        raw = _load_yaml_dict(path)
        overlay = raw.get("overlay")
        if not isinstance(overlay, list):
            continue
        changed = False
        for rule in overlay:
            if not isinstance(rule, dict):
                continue
            if str(rule.get("action") or "").strip() != "findIcon":
                continue
            if str(rule.get("region") or "").strip() != old_primary:
                continue
            rule["region"] = new_primary
            if str(rule.get("search_region") or "").strip() == sn_old:
                rule.pop("search_region", None)
            changed = True
        if not changed:
            continue
        path.write_text(
            yaml.dump(
                raw,
                allow_unicode=True,
                sort_keys=False,
                default_flow_style=False,
                width=100,
            ),
            encoding="utf-8",
        )
        wrote = True
    return wrote
