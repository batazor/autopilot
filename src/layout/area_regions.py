"""area.json helpers: region-name validation, version-aware lookup, dedup.

Schema after the v3 refactor:
- Each screen entry has a base ``regions[]`` list.
- Optional ``versions[]`` declares visual variants. Each version has its own
  ``regions[]`` (overrides + version-only additions) and an optional
  ``removed[]`` list of base region names that are absent in that version.
- A name may repeat between base and version regions (override) but must be
  unique within a single block.
"""
from __future__ import annotations

import math
from typing import Any

from layout.area_versions import (
    VERSION_ID_RE,
    compile_cond,
    get_version_block,
    iter_all_regions,
    pick_active_version,
    resolve_region_with_version,
)


def is_auxiliary_overlay_region(reg: dict[str, Any]) -> bool:
    """True for overlay search zones, tap helpers, or explicit ``overlay_auxiliary`` flags."""
    if reg.get("overlay_auxiliary"):
        return True
    nm = str(reg.get("name", "") or "").strip()
    return nm.endswith("_search") or nm.endswith("_tap")


def _region_names_in(regions: Any) -> list[str]:
    """Non-empty names and aliases of regions in a list (skipping non-dict entries)."""
    if not isinstance(regions, list):
        return []
    out: list[str] = []
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        out.extend(region_names_for(reg))
    return out


def region_names_for(reg: dict[str, Any]) -> list[str]:
    """Canonical region name followed by any same-bbox aliases."""
    out: list[str] = []
    name = str(reg.get("name", "") or "").strip()
    if name:
        out.append(name)
    aliases = reg.get("aliases")
    if isinstance(aliases, list):
        for alias in aliases:
            alias_s = str(alias or "").strip()
            if alias_s and alias_s not in out:
                out.append(alias_s)
    return out


def collect_region_name_counts(doc: dict[str, Any]) -> dict[str, int]:
    """Count non-empty region names across all screen entries (base + every version block).

    Used by autocompletes that want every name a use case might reference.
    """
    counts: dict[str, int] = {}
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        for name in _region_names_in(entry.get("regions")):
            counts[name] = counts.get(name, 0) + 1
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            for name in _region_names_in(ver.get("regions")):
                counts[name] = counts.get(name, 0) + 1
    return counts


def validate_unique_region_names(doc: dict[str, Any]) -> None:
    """Raise ValueError if any single block (base or one version) has duplicate names.

    Names are allowed to repeat ACROSS blocks (a version override re-declares a
    base name on purpose) but never within one ``regions[]`` list.
    """
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        entry_label = f"screen id={entry.get('id')!r} screen_id={entry.get('screen_id')!r}"
        _check_unique_within(entry.get("regions"), f"{entry_label} base")
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            vid = str(ver.get("id", "") or "").strip() or "?"
            _check_unique_within(ver.get("regions"), f"{entry_label} version {vid!r}")


def _check_unique_within(regions: Any, scope: str) -> None:
    counts: dict[str, int] = {}
    for name in _region_names_in(regions):
        counts[name] = counts.get(name, 0) + 1
    dups = sorted(n for n, c in counts.items() if c > 1)
    if dups:
        joined = ", ".join(repr(n) for n in dups)
        raise ValueError(f"Duplicate region name(s) in {scope}: {joined}.")


def _deep_almost_equal(
    a: Any,
    b: Any,
    *,
    rel_tol: float = 1e-9,
    abs_tol: float = 1e-6,
) -> bool:
    """Structural equality with tolerant numeric comparison (JSON-like data)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if type(a) is not type(b):
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)
        return False
    if isinstance(a, dict):
        if set(a.keys()) != set(b.keys()):
            return False
        return all(_deep_almost_equal(a[k], b[k], rel_tol=rel_tol, abs_tol=abs_tol) for k in a)
    if isinstance(a, list):
        if len(a) != len(b):
            return False
        return all(
            _deep_almost_equal(x, y, rel_tol=rel_tol, abs_tol=abs_tol)
            for x, y in zip(a, b, strict=True)
        )
    if isinstance(a, float):
        return isinstance(b, float) and math.isclose(a, b, rel_tol=rel_tol, abs_tol=abs_tol)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=rel_tol, abs_tol=abs_tol)
    return a == b


def _override_matches_base(override: dict[str, Any], base: dict[str, Any]) -> bool:
    """True if a version override is byte-equivalent to its base region (modulo identity)."""
    keys = set(override.keys()) | set(base.keys())
    return all(_deep_almost_equal(override.get(k), base.get(k)) for k in keys)


def dedupe_redundant_version_regions(doc: dict[str, Any]) -> int:
    """Drop version overrides that are byte-identical to the corresponding base region.

    When the annotator saves an override left untouched relative to the default,
    resolution would always return the same geometry/options anyway. Removes
    those overrides; returns how many regions were dropped (mutates ``doc`` in
    place).
    """
    removed = 0
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        base_by_name = {
            str(r.get("name", "") or "").strip(): r
            for r in (entry.get("regions") or [])
            if isinstance(r, dict)
        }
        for ver in entry.get("versions") or []:
            if not isinstance(ver, dict):
                continue
            ver_regions = ver.get("regions")
            if not isinstance(ver_regions, list):
                continue
            kept: list[dict[str, Any]] = []
            for reg in ver_regions:
                if not isinstance(reg, dict):
                    kept.append(reg)
                    continue
                nm = str(reg.get("name", "") or "").strip()
                base = base_by_name.get(nm)
                if base is not None and _override_matches_base(reg, base):
                    removed += 1
                    continue
                kept.append(reg)
            ver["regions"] = kept
    return removed


def region_bbox_for_name(
    doc: dict[str, Any],
    name: str,
    state_flat: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the bbox dict for a region by name.

    With ``state_flat`` provided, lookup honors version selection: the first
    version whose ``cond`` evaluates truthy wins, ``removed[]`` makes the region
    absent, and version ``regions[]`` overrides win over base.
    """
    key = str(name or "").strip()
    if not key:
        return None
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        active = pick_active_version(entry, state_flat) if state_flat is not None else None
        reg = resolve_region_with_version(entry, key, active)
        if reg is None:
            continue
        bbox = reg.get("bbox")
        return bbox if isinstance(bbox, dict) else None
    return None


def validate_versions(doc: dict[str, Any]) -> None:
    """Validate ``versions`` metadata across the document.

    Per entry checks:
      - ``versions[].id`` matches ``^v\\d+$`` and is unique within the entry.
      - ``versions[].cond`` is non-empty and parses as a Python expression.
      - ``versions[].regions[]`` (if present) is a list of dicts with unique names.
      - ``versions[].removed[]`` (if present) is a list of strings, each naming an
        existing base region of the same entry, with no overlap with
        ``versions[].regions[]`` names (cannot both override and remove).

    Raises ``ValueError`` on the first violation.
    """
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        entry_label = f"screen id={entry.get('id')!r} screen_id={entry.get('screen_id')!r}"
        versions = entry.get("versions") or []
        if not isinstance(versions, list):
            raise ValueError(f"{entry_label}: 'versions' must be a list")

        base_names = {
            str(r.get("name", "") or "").strip()
            for r in (entry.get("regions") or [])
            if isinstance(r, dict)
        }
        base_names.discard("")

        seen_ids: set[str] = set()
        for ver in versions:
            if not isinstance(ver, dict):
                raise ValueError(
                    f"{entry_label}: version entry must be an object, got {type(ver).__name__}"
                )
            vid = str(ver.get("id", "") or "").strip()
            if not VERSION_ID_RE.match(vid):
                raise ValueError(
                    f"{entry_label}: version id {vid!r} must match pattern '^v\\d+$' (e.g. 'v2')"
                )
            if vid in seen_ids:
                raise ValueError(f"{entry_label}: duplicate version id {vid!r}")
            seen_ids.add(vid)

            cond = str(ver.get("cond", "") or "").strip()
            if not cond:
                raise ValueError(f"{entry_label}: version {vid!r} has empty 'cond'")
            try:
                compile_cond(cond)
            except SyntaxError as exc:
                raise ValueError(
                    f"{entry_label}: version {vid!r} cond syntax error: {exc}"
                ) from exc

            ver_regions = ver.get("regions")
            if ver_regions is not None and not isinstance(ver_regions, list):
                raise ValueError(
                    f"{entry_label}: version {vid!r} 'regions' must be a list"
                )
            ver_region_names: set[str] = set()
            if isinstance(ver_regions, list):
                for r in ver_regions:
                    if not isinstance(r, dict):
                        raise ValueError(
                            f"{entry_label}: version {vid!r} region entry must be an object"
                        )
                    nm = str(r.get("name", "") or "").strip()
                    if not nm:
                        continue
                    if nm in ver_region_names:
                        raise ValueError(
                            f"{entry_label}: version {vid!r} duplicate region name {nm!r}"
                        )
                    ver_region_names.add(nm)

            removed = ver.get("removed")
            if removed is not None:
                if not isinstance(removed, list):
                    raise ValueError(
                        f"{entry_label}: version {vid!r} 'removed' must be a list of strings"
                    )
                seen_removed: set[str] = set()
                for item in removed:
                    if not isinstance(item, str):
                        raise ValueError(
                            f"{entry_label}: version {vid!r} 'removed' entry must be a string, "
                            f"got {type(item).__name__}"
                        )
                    nm = item.strip()
                    if not nm:
                        continue
                    if nm in seen_removed:
                        raise ValueError(
                            f"{entry_label}: version {vid!r} 'removed' has duplicate {nm!r}"
                        )
                    seen_removed.add(nm)
                    if nm not in base_names:
                        raise ValueError(
                            f"{entry_label}: version {vid!r} 'removed' references "
                            f"non-existent base region {nm!r}"
                        )
                    if nm in ver_region_names:
                        raise ValueError(
                            f"{entry_label}: version {vid!r} cannot both override and remove "
                            f"region {nm!r} — pick one"
                        )


def all_region_names(doc: dict[str, Any]) -> list[str]:
    """Sorted unique non-empty region names across base + every version block.

    Used by autocompletes (DSL editor, scenario authoring).
    """
    return sorted({n for n in collect_region_name_counts(doc)})


__all__ = [
    "all_region_names",
    "collect_region_name_counts",
    "dedupe_redundant_version_regions",
    "get_version_block",
    "is_auxiliary_overlay_region",
    "iter_all_regions",
    "region_bbox_for_name",
    "region_names_for",
    "resolve_region_with_version",
    "validate_unique_region_names",
    "validate_versions",
    "VERSION_ID_RE",
]
