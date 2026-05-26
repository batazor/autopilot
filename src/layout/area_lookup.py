"""Resolve ``area.json`` screen entries and regions by name."""
from __future__ import annotations

from typing import Any

from layout.area_versions import pick_active_version, resolve_region_with_version


def region_tap_hold_ms(region: dict[str, Any] | None) -> int:
    """Read ``tap_hold_ms`` off a region dict, clamped to ``>= 0``.

    Regions whose physical button debounces fast taps (``tap anywhere to
    exit``-style dismiss prompts) set this to opt into a long-press; anyone
    routing a tap through ``BotActions.tap`` should forward the result as
    ``hold_ms=...`` so the controller dispatches a swipe-hold instead of a
    zero-duration ``input tap``.
    """
    if not isinstance(region, dict):
        return 0
    try:
        return max(0, int(region.get("tap_hold_ms") or 0))
    except (TypeError, ValueError):
        return 0


def screen_region_by_name(
    area_doc: dict[str, Any],
    region_name: str,
    state_flat: dict[str, Any] | None = None,
    screen_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Return ``(screen_entry, region_dict)`` for a region ``name``.

    Region names are globally unique in ``area.json``. ``screen_id`` is accepted
    for backwards-compatible call sites, but intentionally ignored: node context
    must not change what a region name resolves to.

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
    _ = screen_id
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        active = pick_active_version(entry, state_flat) if state_flat is not None else None
        reg = resolve_region_with_version(entry, key, active)
        if reg is not None:
            return entry, reg
    return None
