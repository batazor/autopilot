"""Overlay YAML helpers for labeling-time region metadata cleanup."""
from __future__ import annotations

from pathlib import Path
from typing import Any

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
    return [
        aux for aux in (overlay_tap_region_name(name),) if aux in existing_names
    ]


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


def _region_bbox_key(region: dict) -> tuple[float, float, float, float, float] | None:
    bbox = region.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        return (
            round(float(bbox["x"]), 4),
            round(float(bbox["y"]), 4),
            round(float(bbox["width"]), 4),
            round(float(bbox["height"]), 4),
            round(float(bbox.get("rotation") or 0.0), 4),
        )
    except (KeyError, TypeError, ValueError):
        return None


def detect_region_renames(
    old_regions: list[dict],
    new_regions: list[dict],
) -> list[tuple[str, str]]:
    """Pair region renames by identical bbox (Labeling UI keeps geometry on rename)."""
    old_by_bbox: dict[tuple[float, float, float, float, float], str] = {}
    for reg in old_regions:
        if not isinstance(reg, dict):
            continue
        key = _region_bbox_key(reg)
        name = str(reg.get("name") or "").strip()
        if key is None or not name:
            continue
        old_by_bbox[key] = name

    pairs: list[tuple[str, str]] = []
    for reg in new_regions:
        if not isinstance(reg, dict):
            continue
        key = _region_bbox_key(reg)
        new_name = str(reg.get("name") or "").strip()
        if key is None or not new_name:
            continue
        old_name = old_by_bbox.get(key)
        if not old_name or old_name == new_name:
            continue
        pairs.append((old_name, new_name))
    return pairs


def _replace_region_strings_in_tree(obj: object, old: str, new: str) -> bool:
    changed = False
    if isinstance(obj, dict):
        for key, val in list(obj.items()):
            if isinstance(val, str) and val == old:
                obj[key] = new
                changed = True
            elif isinstance(val, (dict, list)) and _replace_region_strings_in_tree(val, old, new):
                changed = True
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            if isinstance(item, str) and item == old:
                obj[idx] = new
                changed = True
            elif isinstance(item, (dict, list)) and _replace_region_strings_in_tree(item, old, new):
                changed = True
    return changed


def _rename_yaml_file_region_refs(path: Path, old: str, new: str) -> bool:
    if not path.is_file():
        return False
    raw = _load_yaml_dict(path)
    if not _replace_region_strings_in_tree(raw, old, new):
        return False
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
    return True


def rename_region_crop_file(
    repo_root: Path,
    reference_repo_rel: str,
    old_name: str,
    new_name: str,
) -> bool:
    from layout.crop_paths import exported_crop_png

    old_path = exported_crop_png(repo_root, reference_repo_rel, old_name)
    new_path = exported_crop_png(repo_root, reference_repo_rel, new_name)
    if not old_path.is_file() or old_path == new_path:
        return False
    new_path.parent.mkdir(parents=True, exist_ok=True)
    if new_path.is_file():
        old_path.unlink()
        return True
    old_path.rename(new_path)
    return True


def rename_region_in_module_files(
    module_dir: Path,
    old_name: str,
    new_name: str,
) -> list[str]:
    """Update ``routes/`` and ``scenarios/`` YAML that reference ``old_name``."""
    touched: list[str] = []
    for sub in ("routes", "scenarios"):
        root = module_dir / sub
        if not root.is_dir():
            continue
        paths = sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml"))
        touched.extend(
            str(path.relative_to(module_dir))
            for path in paths
            if _rename_yaml_file_region_refs(path, old_name, new_name)
        )
    return touched


def cascade_primary_rename_in_regions(
    regions: list[dict[str, Any]],
    old_primary: str,
    new_primary: str,
) -> list[dict[str, Any]]:
    """Rename ``*_tap`` / ``*_search`` aux regions when the primary is renamed."""
    tap_old = overlay_tap_region_name(old_primary)
    tap_new = overlay_tap_region_name(new_primary)
    search_old = overlay_search_region_name(old_primary)
    search_new = overlay_search_region_name(new_primary)
    out: list[dict[str, Any]] = []
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        item = dict(reg)
        name = str(item.get("name") or "").strip()
        if name == tap_old:
            item["name"] = tap_new
        elif name == search_old:
            item["name"] = search_new
        out.append(item)
    return out


def apply_region_rename(
    repo_root: Path,
    *,
    old_name: str,
    new_name: str,
    module_dir: Path | None = None,
    reference_repo_rel: str | None = None,
) -> dict[str, Any]:
    """Propagate a primary region rename from Labeling to overlay + module YAML."""
    old_name = str(old_name or "").strip()
    new_name = str(new_name or "").strip()
    if not old_name or not new_name or old_name == new_name:
        return {"ok": False}
    if old_name.endswith(("_search", "_tap")):
        return {"ok": False, "reason": "aux_region"}

    out: dict[str, Any] = {
        "from": old_name,
        "to": new_name,
        "analyze": rename_findicon_overlay_primary(repo_root, old_name, new_name),
        "module_files": [],
        "crop_renamed": False,
    }
    if module_dir is not None and module_dir.is_dir():
        out["module_files"] = rename_region_in_module_files(module_dir, old_name, new_name)
    if reference_repo_rel:
        out["crop_renamed"] = rename_region_crop_file(
            repo_root, reference_repo_rel, old_name, new_name
        )
    return out


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
