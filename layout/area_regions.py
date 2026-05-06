"""area.json helpers: unique region names and lookup by name (shared by UI and worker)."""

from __future__ import annotations

from typing import Any


def collect_region_name_counts(doc: dict[str, Any]) -> dict[str, int]:
    """Count non-empty region names across all screen entries."""
    counts: dict[str, int] = {}
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            name = str(reg.get("name", "") or "").strip()
            if not name:
                continue
            counts[name] = counts.get(name, 0) + 1
    return counts


def duplicate_region_names(doc: dict[str, Any]) -> list[str]:
    """Return sorted region names that appear more than once."""
    counts = collect_region_name_counts(doc)
    return sorted(n for n, c in counts.items() if c > 1)


def validate_unique_region_names(doc: dict[str, Any]) -> None:
    """Raise ValueError if two or more regions share the same non-empty name."""
    dups = duplicate_region_names(doc)
    if dups:
        joined = ", ".join(repr(n) for n in dups)
        raise ValueError(
            f"Duplicate region name(s): {joined}. Each region name must be unique across area.json."
        )


def region_bbox_for_name(doc: dict[str, Any], name: str) -> dict[str, Any] | None:
    """Return the bbox dict for a globally unique region name, or None if missing."""
    key = str(name or "").strip()
    if not key:
        return None
    for entry in doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            if str(reg.get("name", "") or "").strip() != key:
                continue
            bbox = reg.get("bbox")
            if isinstance(bbox, dict):
                return bbox
            return None
    return None
