"""Parse in-game building title text such as ``Cookhouse Lv. 1``."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from config.buildings import BuildingDef

# Level token across locales + common Tesseract homoglyphs. EN "Lv."/"Level";
# RU "ур."/"уровень". On degraded scrcpy H.264 frames Tesseract routinely renders
# Cyrillic "ур" as the Latin homoglyphs "yp" (у→y, р→p), so accept that spelling.
_LEVEL_TOKEN = r"(?:Lv\.?|Level|ур\.?|уровень|yp\.?)"

# End on a word boundary (not ``$``) so trailing UI artefacts after the level —
# the «Топка Ур. 3 !» urgency badge, stray OCR punctuation — don't break the
# match. Mirrors the trailing-tolerant ``\b`` in _SHELTER_NAME_RE below.
_BUILDING_NAME_RE = re.compile(
    rf"^\s*(?P<name>.+?)\s+{_LEVEL_TOKEN}\s*\.?\s*(?P<level>\d+)\b",
    re.IGNORECASE,
)
# Shelter is multi-instance ("Shelter 2"); the «Белая мгла» build localises it to
# "Барак". OCR may merge the instance number into the name ("Барак2") and prefix
# the plate with junk ("›.", "——й лени, ="), so the inner separators are optional
# and this is matched with ``.search`` (no ``^`` anchor — see the parser below).
_SHELTER_NAME_RE = re.compile(
    rf"(?:Shelter|Барак)\s*(?P<number>\d+)\s*{_LEVEL_TOKEN}\s*\.?\s*(?P<level>\d+)\b",
    re.IGNORECASE,
)


def normalise_building_lookup_text(value: str) -> str:
    # Keep Cyrillic so RU plates ("Барак") survive normalisation instead of
    # collapsing to "" — the registry match / alias lookup needs the letters.
    return re.sub(r"[^a-z0-9а-яё]+", "", (value or "").lower())


# RU building name → canonical English registry name. The «Белая мгла»
# (com.gof.globalru) build renders Cyrillic plates while the registry is English,
# so the level reader (and any name→id lookup) needs this bridge. Keys are written
# readable and normalised at import (lower + strip spaces/punct) to match
# `normalise_building_lookup_text`. Safe by design: a wrong or missing RU name
# simply fails to match (no level recorded) — it can never mis-map to a different
# building — so this is meant to be filled/corrected against live RU plates.
# Latin-homoglyph variants (e.g. «Топка»→"Tonka") are only needed where OCR flips
# Cyrillic→Latin; `rus`-only OCR (config.ocr.catalog_lang) makes that rare.
_RU_NAME_TO_CANON = {
    # — power / governance —
    "Печь": "Furnace", "Топка": "Furnace", "Tonka": "Furnace",
    "Посольство": "Embassy",
    "Лазарет": "Infirmary",
    "Командный центр": "Command Center",
    "Склад": "Storehouse", "Хранилище": "Storehouse",
    "Исследовательский центр": "Research Center", "Научный центр": "Research Center",
    "Военная академия": "War Academy",
    # — troop camps —
    "Лагерь пехоты": "Infantry Camp",
    "Лагерь копейщиков": "Lancer Camp",
    "Лагерь стрелков": "Marksman Camp",
    # — resource production —
    "Лесопилка": "Sawmill",
    "Угольная шахта": "Coal Mine",
    "Железный рудник": "Iron Mine",
    "Хижина охотника": "Hunter's Hut",
    "Кухня": "Cookhouse", "Столовая": "Cookhouse",
    # — capacity / civic / defence —
    "Барак": "Shelter",
    "Дом вождя": "Chief's House",
    "Зал героев": "Hero Hall",
    "Арена": "Arena",
    "Баррикада": "Barricade",
    "Маяк": "Lighthouse",
    # — lower confidence: verify the exact RU wording against a live plate —
    "Клетка зверя": "Beast Cage",
    "Кристальная лаборатория": "Crystal Laboratory",
    "Академия рассвета": "Dawn Academy",
}
# normalised RU (and homoglyph) → canonical EN, used by the level reader
# (building_by_ocr_name). Derived from the readable map above.
_RU_NAME_ALIASES = {
    normalise_building_lookup_text(_ru): _canon for _ru, _canon in _RU_NAME_TO_CANON.items()
}


def ru_aliases_for_building(name: str) -> list[str]:
    """Readable RU localisations of a canonical English building name, for the
    screen-detect ``contains`` list (``screen_graph`` building generator).

    Cyrillic only — Latin-homoglyph spellings (e.g. "Tonka") are reader-side
    OCR-repair, not real in-game text, so they must not seed detection. Returns
    ``[]`` for an unmapped building (detection stays English-only, no regression).
    """
    target = normalise_building_lookup_text(name)
    return [
        ru
        for ru, canon in _RU_NAME_TO_CANON.items()
        if normalise_building_lookup_text(canon) == target
        and any("Ѐ" <= ch <= "ӿ" for ch in ru)
    ]


def building_by_ocr_name(
    name: str,
    buildings: Iterable[BuildingDef],
) -> BuildingDef | None:
    wanted = normalise_building_lookup_text(name)
    if not wanted:
        return None
    alias = _RU_NAME_ALIASES.get(wanted)
    if alias is not None:
        wanted = normalise_building_lookup_text(alias)
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
    shelter_match = _SHELTER_NAME_RE.search(text or "")
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
