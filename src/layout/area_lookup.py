"""Resolve ``area.json`` screen entries and regions by name."""
from __future__ import annotations

from collections import OrderedDict
from typing import Any

from layout.area_versions import resolve_region_by_name

# id(area_doc) -> {region_name | alias -> screen_entry}. Region names are
# globally unique across screens, so a single dict gives us O(1) routing from
# region name to its owning screen entry. The cache is keyed by ``id`` (small
# bounded LRU) because ``dict`` is not hashable for a WeakKeyDictionary.
_REGION_TO_SCREEN_CACHE: OrderedDict[int, dict[str, dict[str, Any]]] = OrderedDict()
_REGION_TO_SCREEN_CACHE_MAX = 16


def _region_to_screen_index(area_doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    key = id(area_doc)
    cached = _REGION_TO_SCREEN_CACHE.get(key)
    if cached is not None:
        _REGION_TO_SCREEN_CACHE.move_to_end(key)
        return cached
    idx: dict[str, dict[str, Any]] = {}

    def _register(name: str, entry: dict[str, Any]) -> None:
        nm = name.strip()
        if nm and nm not in idx:
            idx[nm] = entry

    def _register_regions(entry: dict[str, Any], regions: Any) -> None:
        for reg in regions or []:
            if not isinstance(reg, dict):
                continue
            _register(str(reg.get("name", "") or ""), entry)
            aliases = reg.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    if isinstance(alias, str):
                        _register(alias, entry)

    screens = [e for e in (area_doc.get("screens") or []) if isinstance(e, dict)]
    for entry in screens:
        _register_regions(entry, entry.get("regions"))

    _REGION_TO_SCREEN_CACHE[key] = idx
    _REGION_TO_SCREEN_CACHE.move_to_end(key)
    while len(_REGION_TO_SCREEN_CACHE) > _REGION_TO_SCREEN_CACHE_MAX:
        _REGION_TO_SCREEN_CACHE.popitem(last=False)
    return idx


def clear_region_lookup_cache() -> None:
    """Drop the region-to-screen index (call after :func:`load_area_doc` reload)."""
    _REGION_TO_SCREEN_CACHE.clear()


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

    Region names are globally unique in ``area.json``. ``screen_id`` and
    ``state_flat`` are accepted for backwards-compatible call sites, but
    intentionally ignored: neither node context nor player state changes what a
    region name resolves to.
    """
    key = str(region_name or "").strip()
    if not key:
        return None
    _ = screen_id
    _ = state_flat
    entry = _region_to_screen_index(area_doc).get(key)
    if entry is None:
        return None
    reg = resolve_region_by_name(entry, key)
    if reg is None:
        return None
    return entry, reg
