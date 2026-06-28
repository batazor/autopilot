"""Pure parsers for OCR'd ``<domain>.read.*`` fields → ``owned`` dicts."""
from __future__ import annotations

from games.wos.core.readers.parse import (
    _coerce_int,
    parse_owned_flat,
    parse_owned_nested,
)


def test_coerce_int_strips_ocr_noise() -> None:
    assert _coerce_int("12") == 12
    assert _coerce_int(" Lv.30 ") == 30      # strip surrounding glyphs
    assert _coerce_int("0") == 0
    assert _coerce_int("") is None
    assert _coerce_int("??") is None
    assert _coerce_int(None) is None


def test_parse_owned_flat_collects_entities() -> None:
    fields = {
        "charms.read.infantry_1": "5",
        "charms.read.marksman_6": "2",
        "charms.read.lancer_3": "Lv 3",       # noisy
        "pets.read.snow_leopard.level": "30",  # wrong domain/shape → ignored
        "other.key": "9",
    }
    assert parse_owned_flat(fields, domain="charms") == {
        "infantry_1": 5, "marksman_6": 2, "lancer_3": 3,
    }


def test_parse_owned_flat_ignores_nested_shape() -> None:
    fields = {"gear.read.gloves_belt_infantry.enhance": "5"}  # nested → not flat
    assert parse_owned_flat(fields, domain="gear") == {}


def test_parse_owned_nested_groups_by_entity() -> None:
    fields = {
        "hero_gear.read.gloves_belt_infantry.enhance": "45",
        "hero_gear.read.gloves_belt_infantry.mastery": "12",
        "hero_gear.read.gloves_belt_infantry.widget": "5",
        "hero_gear.read.goggles_boots_lancer.enhance": "48",
    }
    assert parse_owned_nested(fields, domain="hero_gear") == {
        "gloves_belt_infantry": {"enhance": 45, "mastery": 12, "widget": 5},
        "goggles_boots_lancer": {"enhance": 48},
    }


def test_parse_owned_nested_require_filters_empty_slots() -> None:
    fields = {
        "pets.read.snow_leopard.level": "30",
        "pets.read.snow_leopard.refine": "5",
        "pets.read.locked_slot.skill": "0",   # no level → a locked/empty slot
    }
    assert parse_owned_nested(fields, domain="pets", require="level") == {
        "snow_leopard": {"level": 30, "refine": 5},
    }
