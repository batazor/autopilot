"""Parse in-game building title text such as ``Cookhouse Lv. 1``."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from config.buildings import BuildingDef

_BUILDING_NAME_RE = re.compile(
    r"^\s*(?P<name>.+?)\s+(?:Lv\.?|Level)\s*\.?\s*(?P<level>\d+)\s*$",
    re.IGNORECASE,
)
_SHELTER_NAME_RE = re.compile(
    r"^\s*Shelter\s+(?P<number>\d+)\s+(?:Lv\.?|Level)\s*\.?\s*(?P<level>\d+)\b",
    re.IGNORECASE,
)


def normalise_building_lookup_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def building_by_ocr_name(
    name: str,
    buildings: Iterable[BuildingDef],
) -> BuildingDef | None:
    wanted = normalise_building_lookup_text(name)
    if not wanted:
        return None
    for building in buildings:
        if normalise_building_lookup_text(building.name) == wanted:
            return building
    return None


def parse_building_name_level(
    text: str,
    buildings: Iterable[BuildingDef],
) -> tuple[BuildingDef, int] | None:
    parsed = parse_building_name_level_instance(text, buildings)
    if parsed is None:
        return None
    building, level, _instance_id = parsed
    return building, level


def parse_building_name_level_instance(
    text: str,
    buildings: Iterable[BuildingDef],
) -> tuple[BuildingDef, int, str] | None:
    shelter_match = _SHELTER_NAME_RE.match(text or "")
    if shelter_match:
        building = building_by_ocr_name("Shelter", buildings)
        if building is None:
            return None
        try:
            number = int(shelter_match.group("number"))
            level = int(shelter_match.group("level"))
        except ValueError:
            return None
        if number <= 0 or level <= 0:
            return None
        return building, level, f"{building.id}_{number}"

    match = _BUILDING_NAME_RE.match(text or "")
    if not match:
        return None
    building = building_by_ocr_name(match.group("name"), buildings)
    if building is None:
        return None
    try:
        level = int(match.group("level"))
    except ValueError:
        return None
    if level <= 0:
        return None
    return building, level, building.id
