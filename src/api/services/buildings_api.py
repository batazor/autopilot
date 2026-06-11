"""Buildings reference payload for the Next.js /buildings page.

Single source of truth is the per-game YAML registry under
``games/<game>/db/buildings/`` (loaded by ``config.buildings``). The page
renders straight from this — no data is duplicated in the frontend.

The dependency graph is explicit in the YAML (``requires:`` list of
``{building, level}`` edges, derived once by
``scripts/derive_building_requires.py`` from the free-text prerequisites). This
serializer just passes it through — no text parsing at request time.
"""
from __future__ import annotations

from typing import Any

from config.buildings import get_building_registry


def get_buildings_payload() -> dict[str, Any]:
    registry = get_building_registry()
    buildings: list[dict[str, Any]] = []

    for b in registry.buildings:
        levels = {
            str(level): {
                "prerequisites": str(raw.get("prerequisites") or "").strip(),
                "construction_time": raw.get("construction_time"),
                "building_power": raw.get("building_power"),
                "build_cost": raw.get("build_cost") or [],
            }
            for level, raw in sorted(b.requirements_by_level.items())
        }
        max_level = max(b.requirements_by_level) if b.requirements_by_level else None
        buildings.append(
            {
                "id": b.id,
                "name": b.name,
                "category": b.category,
                "max_level": max_level,
                "requires": [
                    {"building": r.building, "level": r.level} for r in b.requires
                ],
                "requirements_by_level": levels,
            }
        )

    return {
        "game": "wos",
        "hub_id": "furnace",
        "buildings": buildings,
    }
