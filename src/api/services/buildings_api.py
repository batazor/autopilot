"""Buildings reference payload for the Next.js /buildings page.

Single source of truth is the per-game YAML registry under
``games/<game>/db/buildings/`` (loaded by ``config.buildings``). The page
renders straight from this — no data is duplicated in the frontend.
"""
from __future__ import annotations

from typing import Any

from config.buildings import get_building_registry


def get_buildings_payload() -> dict[str, Any]:
    registry = get_building_registry()
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
                "requirements_by_level": levels,
            }
        )

    return {
        "game": "wos",
        "hub_id": "furnace",
        "buildings": buildings,
    }
