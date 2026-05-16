"""Resolve ``area.json`` screen entries and regions by name."""
from __future__ import annotations

from typing import Any

from layout.area_versions import pick_active_version, resolve_region_with_version


def screen_region_by_name(
    area_doc: dict[str, Any],
    region_name: str,
    state_flat: dict[str, Any] | None = None,
    screen_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return ``(screen_entry, region_dict)`` for a region ``name``.

    When ``screen_id`` is provided, lookup is scoped to matching
    ``screens[].screen_id`` entries. That lets different screens reuse the same
    OmniParser-derived button names without the first entry in ``area.json``
    shadowing the active screen.

    With ``state_flat`` provided, the lookup honors the screen-entry's
    ``versions`` metadata: the first version whose ``cond`` is truthy activates,
    its ``regions[]`` overrides win over the base, and a name in
    ``versions[].removed`` is treated as absent.

    With ``state_flat=None`` the lookup matches only against the base
    ``regions[]`` (default-version semantics).
    """
    key = str(region_name or "").strip()
    if not key:
        return None
    entries = [entry for entry in area_doc.get("screens") or [] if isinstance(entry, dict)]
    all_entries = entries
    screen_key = str(screen_id or "").strip()
    if screen_key:
        scoped = [
            entry
            for entry in entries
            if str(entry.get("screen_id") or "").strip() == screen_key
        ]
        if scoped:
            entries = scoped
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        active = pick_active_version(entry, state_flat) if state_flat is not None else None
        reg = resolve_region_with_version(entry, key, active)
        if reg is not None:
            return entry, reg
    if screen_key:
        for entry in all_entries:
            if str(entry.get("screen_id") or "").strip():
                continue
            active = pick_active_version(entry, state_flat) if state_flat is not None else None
            reg = resolve_region_with_version(entry, key, active)
            if reg is not None:
                return entry, reg
    return None
