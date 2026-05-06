"""Keep ``analyze.yaml`` overlay keys in sync with optional ``*_search`` / ``*_tap`` regions."""

from __future__ import annotations

from pathlib import Path

import yaml


def overlay_search_region_name(primary: str) -> str:
    return f"{str(primary).strip()}_search"


def overlay_tap_region_name(primary: str) -> str:
    return f"{str(primary).strip()}_tap"


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
    path = repo_root / "references" / "analyze.yaml"
    if not path.is_file():
        return False
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return False
    overlay = raw.get("overlay")
    if not isinstance(overlay, list):
        return False
    primary = str(primary_region or "").strip()
    if not primary:
        return False
    sn = overlay_search_region_name(primary)
    tn = overlay_tap_region_name(primary)
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
