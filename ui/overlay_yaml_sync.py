"""Keep ``analyze.yaml`` overlay keys in sync with optional ``*_search`` / ``*_tap`` regions."""

from __future__ import annotations

from pathlib import Path

import yaml


def overlay_search_region_name(primary: str) -> str:
    return f"{str(primary).strip()}_search"


def overlay_tap_region_name(primary: str) -> str:
    return f"{str(primary).strip()}_tap"


def _load_yaml_dict(path: Path) -> dict:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return raw if isinstance(raw, dict) else {}


def _iter_analyze_sources(repo_root: Path) -> list[Path]:
    """Return all YAML files that can contain overlay rules.

    - Legacy: only ``references/analyze.yaml`` (no manifest include)
    - Manifest: ``references/analyze.yaml`` with ``include: [...]`` points to rule files
    """
    manifest = repo_root / "references" / "analyze.yaml"
    if not manifest.is_file():
        return []

    raw = _load_yaml_dict(manifest)
    inc = raw.get("include")
    if not isinstance(inc, list) or not inc:
        return [manifest]

    out: list[Path] = []
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
    use_tap: bool,
) -> bool:
    """Set or remove ``search_region`` / ``tap_region`` on the matching overlay rule.

    Returns True if ``analyze.yaml`` was written.
    """
    primary = str(primary_region or "").strip()
    if not primary:
        return False
    sn = overlay_search_region_name(primary)
    tn = overlay_tap_region_name(primary)
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
            if use_search:
                rule["search_region"] = sn
            else:
                rule.pop("search_region", None)
            if use_tap:
                rule["tap_region"] = tn
            else:
                rule.pop("tap_region", None)
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
        return True
    return False


def rename_findicon_overlay_primary(
    repo_root: Path,
    old_primary: str,
    new_primary: str,
) -> bool:
    """Sync ``analyze.yaml`` after a primary region rename in ``area.json``.

    Sets matching ``findIcon`` rule ``region`` to ``new_primary``. Rewrites ``search_region`` /
    ``tap_region`` when they still equal ``{old}_search`` / ``{old}_tap``.
    """
    old_primary = str(old_primary or "").strip()
    new_primary = str(new_primary or "").strip()
    if not old_primary or not new_primary or old_primary == new_primary:
        return False
    sn_old = overlay_search_region_name(old_primary)
    sn_new = overlay_search_region_name(new_primary)
    tn_old = overlay_tap_region_name(old_primary)
    tn_new = overlay_tap_region_name(new_primary)
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
                rule["search_region"] = sn_new
            if str(rule.get("tap_region") or "").strip() == tn_old:
                rule["tap_region"] = tn_new
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
