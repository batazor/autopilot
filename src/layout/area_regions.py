"""area.json helpers: region-name validation and lookup.

Each screen entry has a single ``regions[]`` list; region names (plus aliases)
are globally unique across the document.
"""
from __future__ import annotations

from typing import Any


def is_auxiliary_overlay_region(reg: dict[str, Any]) -> bool:
    """True for overlay search zones, tap helpers, or explicit ``overlay_auxiliary`` flags."""
    if reg.get("overlay_auxiliary"):
        return True
    nm = str(reg.get("name", "") or "").strip()
    return nm.endswith(("_search", "_tap"))


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


def _index_regions_by_name(regions: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(regions, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        name = str(reg.get("name", "") or "").strip()
        if name:
            out[name] = reg
        aliases = reg.get("aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                alias_s = str(alias or "").strip()
                if alias_s:
                    out[alias_s] = reg
    return out


def resolve_region_by_name(
    screen_entry: dict[str, Any],
    region_name: str,
) -> dict[str, Any] | None:
    """Resolve ``region_name`` (or one of its aliases) in the entry's regions."""
    key = str(region_name or "").strip()
    if not key:
        return None
    return _index_regions_by_name(screen_entry.get("regions")).get(key)


def effective_ocr_for_region(
    screen_entry: dict[str, Any],
    region: dict[str, Any],
) -> str:
    """Reference image for any region of the entry: the entry's ``ocr``."""
    return str(screen_entry.get("ocr") or "").strip()


def iter_all_regions(
    screen_entry: dict[str, Any],
) -> list[tuple[dict[str, Any], str | None]]:
    """Yield ``(region, None)`` for every region of the entry."""
    return [
        (reg, None)
        for reg in screen_entry.get("regions") or []
        if isinstance(reg, dict)
    ]


def collect_region_name_counts(doc: dict[str, Any]) -> dict[str, int]:
    """Count non-empty region names across all screen entries.

    Used by autocompletes that want every name a use case might reference.
    """
    counts: dict[str, int] = {}
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        for name in _region_names_in(entry.get("regions")):
            counts[name] = counts.get(name, 0) + 1
    return counts


def validate_unique_region_names(doc: dict[str, Any]) -> None:
    """Raise ValueError if any screen entry has duplicate region names."""
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        entry_label = f"screen id={entry.get('id')!r} screen_id={entry.get('screen_id')!r}"
        _check_unique_within(entry.get("regions"), f"{entry_label} base")


def _check_unique_within(regions: Any, scope: str) -> None:
    counts: dict[str, int] = {}
    for name in _region_names_in(regions):
        counts[name] = counts.get(name, 0) + 1
    dups = sorted(n for n, c in counts.items() if c > 1)
    if dups:
        joined = ", ".join(repr(n) for n in dups)
        msg = f"Duplicate region name(s) in {scope}: {joined}."
        raise ValueError(msg)


def region_bbox_for_name(
    doc: dict[str, Any],
    name: str,
    state_flat: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the bbox dict for a region by name (``state_flat`` is ignored)."""
    _ = state_flat
    key = str(name or "").strip()
    if not key:
        return None
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        reg = resolve_region_by_name(entry, key)
        if reg is None:
            continue
        bbox = reg.get("bbox")
        return bbox if isinstance(bbox, dict) else None
    return None


def all_region_names(doc: dict[str, Any]) -> list[str]:
    """Sorted unique non-empty region names across base + every version block.

    Used by autocompletes (DSL editor, scenario authoring).
    """
    return sorted(set(collect_region_name_counts(doc)))


__all__ = [
    "all_region_names",
    "collect_region_name_counts",
    "effective_ocr_for_region",
    "is_auxiliary_overlay_region",
    "iter_all_regions",
    "region_bbox_for_name",
    "region_names_for",
    "resolve_region_by_name",
    "validate_unique_region_names",
]
