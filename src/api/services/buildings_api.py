"""Buildings reference payload for the Next.js /buildings page.

Single source of truth is the per-game YAML registry under
``games/<game>/db/buildings/`` (loaded by ``config.buildings``). The page
renders straight from this — no data is duplicated in the frontend.

The per-level ``prerequisites`` are free text (e.g. "Embassy Lv. 8 Infirmary
Lv. 1"). ``_unlock_requirements`` resolves them into structured
``{building, level}`` refs by matching known building names, so the frontend
can draw a real dependency graph instead of re-parsing strings.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from config.buildings import get_building_registry

if TYPE_CHECKING:
    from config.buildings import BuildingDef

# British/American and punctuation variants seen in the wiki source text that
# don't match the canonical building names verbatim.
_ALIASES = {
    "command centre": "command_center",
}

_NUM_RE = re.compile(r"\d+")


def _normalize(text: str) -> str:
    return text.replace("’", "'").replace("`", "'").lower().strip()


def _name_index(buildings: tuple[BuildingDef, ...]) -> list[tuple[str, str]]:
    """(normalized name, id) pairs, longest name first so multi-word names win."""
    pairs = {_normalize(b.name): b.id for b in buildings}
    pairs.update(_ALIASES)
    return sorted(pairs.items(), key=lambda kv: -len(kv[0]))


def _refs_in_text(text: str, names: list[tuple[str, str]]) -> dict[str, int]:
    """Resolve a prerequisites string to {building_id: required_level}."""
    norm = _normalize(text)
    refs: dict[str, int] = {}
    for name, bid in names:
        start = 0
        while (i := norm.find(name, start)) != -1:
            tail = norm[i + len(name) : i + len(name) + 14]
            m = _NUM_RE.search(tail)
            level = int(m.group()) if m else 1
            refs[bid] = max(refs.get(bid, 0), level)
            # Blank the match so a shorter name can't re-match inside it
            # (e.g. "Marksman Camp" inside "Fire Crystal Marksman Camp").
            norm = norm[:i] + (" " * len(name)) + norm[i + len(name) :]
            start = i + len(name)
    return refs


def _unlock_requirements(
    b: BuildingDef, names: list[tuple[str, str]]
) -> list[dict[str, Any]]:
    """Earliest (unlock-gate) requirement per referenced building.

    Walks the building's levels ascending; the first level that references a
    prerequisite building records the gate (that building at the listed level).
    """
    gates: dict[str, int] = {}
    for level in sorted(b.requirements_by_level):
        text = str(b.requirements_by_level[level].get("prerequisites") or "")
        if not text:
            continue
        for bid, req_level in _refs_in_text(text, names).items():
            if bid == b.id or bid in gates:
                continue
            gates[bid] = req_level
    return [{"building": bid, "level": lvl} for bid, lvl in gates.items()]


def get_buildings_payload() -> dict[str, Any]:
    registry = get_building_registry()
    names = _name_index(registry.buildings)
    buildings: list[dict[str, Any]] = []

    for b in registry.buildings:
        levels: dict[str, Any] = {}
        for level in sorted(b.requirements_by_level):
            raw = b.requirements_by_level[level]
            levels[str(level)] = {
                "prerequisites": str(raw.get("prerequisites") or "").strip(),
                "construction_time": raw.get("construction_time"),
                "building_power": raw.get("building_power"),
                "build_cost": raw.get("build_cost") or [],
            }
        max_level = max(b.requirements_by_level) if b.requirements_by_level else None
        buildings.append(
            {
                "id": b.id,
                "name": b.name,
                "category": b.category,
                "max_level": max_level,
                "requires": _unlock_requirements(b, names),
                "requirements_by_level": levels,
            }
        )

    return {
        "game": "wos",
        "hub_id": "furnace",
        "buildings": buildings,
    }
