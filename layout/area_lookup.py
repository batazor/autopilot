"""Resolve ``area.json`` screen entries and regions by name."""

from __future__ import annotations

from typing import Any

from layout.area_versions import pick_active_version, resolve_region_with_version


def screen_region_by_name(
    area_doc: dict[str, Any],
    region_name: str,
    state_flat: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return ``(screen_entry, region_dict)`` for a region ``name``.

    When ``state_flat`` is provided, the lookup honors the screen-entry's
    ``versions`` metadata: the first version whose ``cond`` is truthy activates,
    and a ``_<version_id>``-suffixed override (e.g. ``promote_btn_v2``) is
    returned in preference to the default region. With ``state_flat=None`` the
    lookup matches only by exact name (default-version semantics).
    """
    key = str(region_name or "").strip()
    if not key:
        return None
    for entry in area_doc.get("screens") or []:
        if not isinstance(entry, dict):
            continue
        if state_flat is not None:
            active = pick_active_version(entry, state_flat)
            reg = resolve_region_with_version(entry, key, active)
            if reg is not None:
                return entry, reg
            continue
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            if str(reg.get("name", "") or "").strip() != key:
                continue
            return entry, reg
    return None
