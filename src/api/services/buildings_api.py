"""Buildings reference payload for the Next.js /buildings page.

Single source of truth is the per-game YAML registry under
``games/<game>/db/buildings/`` (loaded by ``config.buildings``). The page
renders straight from this — no data is duplicated in the frontend.

Two dependency views are emitted:
- per-building ``requires`` — explicit unlock gates from the YAML (derived once
  by ``scripts/derive_building_requires.py``).
- per-level ``requires`` inside each ``requirements_by_level`` entry — resolved
  here from that level's free-text prerequisites, so the frontend can draw a
  node per (building, level) without re-parsing strings.
"""
from __future__ import annotations

from typing import Any

from config.building_deps import name_index, refs_in_text
from config.buildings import get_building_registry


def get_buildings_payload() -> dict[str, Any]:
    registry = get_building_registry()
    names = name_index(registry.buildings)
    buildings: list[dict[str, Any]] = []

    for b in registry.buildings:
        levels: dict[str, Any] = {}
        for level, raw in sorted(b.requirements_by_level.items()):
            text = str(raw.get("prerequisites") or "").strip()
            level_requires = [
                {"building": dep, "level": lvl}
                for dep, lvl in refs_in_text(text, names).items()
                if dep != b.id
            ]
            levels[str(level)] = {
                "prerequisites": text,
                "construction_time": raw.get("construction_time"),
                "building_power": raw.get("building_power"),
                "build_cost": raw.get("build_cost") or [],
                "requires": level_requires,
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
