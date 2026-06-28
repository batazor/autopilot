"""RU + homoglyph coverage for the building-title parser (config.building_name_parser).

The English «Lv.» path was only covered indirectly; these lock the «Белая мгла»
(com.gof.globalru) build: Cyrillic "Барак" = Shelter, the "ур." level token, and
the "ур"→"yp" Tesseract homoglyph observed on degraded scrcpy frames (the OCR of
the live bs4 quest plate read "Барак2 … yp. 4").
"""
from __future__ import annotations

import pytest

from config.building_name_parser import (
    building_by_ocr_name,
    normalise_building_lookup_text,
    parse_building_name_level_instance,
)
from config.buildings import get_building_registry


@pytest.fixture(scope="module")
def buildings():
    return get_building_registry().buildings


@pytest.mark.parametrize(
    ("text", "expected_id", "expected_level", "expected_instance"),
    [
        # English — unchanged behaviour.
        ("Furnace Lv. 1", "furnace", 1, "furnace"),
        ("Shelter 2 Lv. 4", "shelter", 4, "shelter_2"),
        # RU «Белая мгла»: Барак = Shelter, "ур." token.
        ("Барак 2 ур. 4", "shelter", 4, "shelter_2"),
        ("Барак 3 Ур 5", "shelter", 5, "shelter_3"),
        # OCR homoglyph "ур"→"yp" + merged instance number "Барак2" (real sample).
        ("Барак2 yp. 4", "shelter", 4, "shelter_2"),
        # No instance number → base shelter via the general regex + alias.
        ("Барак ур. 6", "shelter", 6, "shelter"),
        # Real rus-only client OCR off live bs4 (leading junk "Е пн", trailing
        # "!"): the .search-based shelter regex recovers it where ^-anchored match
        # could not.
        ("Е пн Барак 2 Ур. 2!", "shelter", 2, "shelter_2"),
        # RU Furnace renders as «Топка» (live) or «Печь»; trailing urgency "!".
        ("Топка Ур. 3 !", "furnace", 3, "furnace"),
        ("Печь ур. 2", "furnace", 2, "furnace"),
        # Live bs5 OCR: rus+eng renders «Топка» as the Latin homoglyph "Tonka"
        # (leading "‘", trailing "."), the name-side analogue of «ур»→«yp».
        ("‘Tonka Ур. 3.", "furnace", 3, "furnace"),
    ],
)
def test_parse_ru_and_homoglyph(
    buildings, text, expected_id, expected_level, expected_instance
):
    parsed = parse_building_name_level_instance(text, buildings)
    assert parsed is not None, text
    building, level, instance_id = parsed
    assert building.id == expected_id
    assert level == expected_level
    assert instance_id == expected_instance


def test_normalise_keeps_cyrillic():
    assert normalise_building_lookup_text("Барак") == "барак"
    assert normalise_building_lookup_text("Барак 2!") == "барак2"


def test_barak_alias_resolves_to_shelter(buildings):
    b = building_by_ocr_name("Барак", buildings)
    assert b is not None and b.id == "shelter"


def test_unmapped_ru_name_is_none(buildings):
    # A Cyrillic word with no RU alias and no registry match resolves to None:
    # the level token parses, but the name doesn't map to any building.
    assert parse_building_name_level_instance("Зззз ур. 2", buildings) is None


def test_every_ru_alias_targets_a_real_building(buildings):
    """Each RU→EN alias value must resolve to a registry building — guards against
    an English-side typo (e.g. "Command Centre") that would silently never match.
    Does NOT assert the RU key is the correct in-game name (only a live plate can)."""
    from config.building_name_parser import _RU_NAME_ALIASES

    for ru_key, canon in _RU_NAME_ALIASES.items():
        assert building_by_ocr_name(canon, buildings) is not None, (ru_key, canon)


@pytest.mark.parametrize(
    ("text", "expected_id"),
    [
        ("Лагерь пехоты Ур. 7", "infantry_camp"),
        ("Посольство ур. 5", "embassy"),
        ("Командный центр Ур. 9", "command_center"),
        ("Угольная шахта ур. 4", "coal_mine"),
        ("Дом вождя Ур. 6", "chiefs_house"),
    ],
)
def test_multiword_ru_building_names(buildings, text, expected_id):
    parsed = parse_building_name_level_instance(text, buildings)
    assert parsed is not None, text
    assert parsed[0].id == expected_id


def test_ru_aliases_for_building_readable_cyrillic_only():
    from config.building_name_parser import ru_aliases_for_building

    # Readable Cyrillic forms, for screen-detect contains. "Tonka" (Latin
    # homoglyph, reader-only) must NOT leak into detection.
    assert ru_aliases_for_building("Furnace") == ["Печь", "Топка"]
    assert ru_aliases_for_building("Embassy") == ["Посольство"]
    assert ru_aliases_for_building("Infantry Camp") == ["Лагерь пехоты"]
    # Unmapped → empty (detection stays English-only, no regression).
    assert ru_aliases_for_building("Suggestion Box") == []
