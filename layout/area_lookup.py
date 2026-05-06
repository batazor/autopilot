"""Resolve ``area.json`` screen entries and regions by name."""

from __future__ import annotations

from typing import Any


def screen_region_by_name(
    area_doc: dict[str, Any],
    region_name: str,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return ``(screen_entry, region_dict)`` for a globally unique region ``name``."""
    key = str(region_name or "").strip()
    if not key:
        return None
    for entry in area_doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            if str(reg.get("name", "") or "").strip() != key:
                continue
            return entry, reg
    return None
