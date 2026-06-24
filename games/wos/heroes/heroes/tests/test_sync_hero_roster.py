"""Unit tests for the hero-roster projection (``build_roster``) + the producer →
consumer contract with the resource allocator's ``_parse_hero_roster``."""

from __future__ import annotations

import json

from games.wos.core.resources.adapter import _parse_hero_roster
from games.wos.heroes.heroes.sync_hero_roster import build_roster

ROLE_OF = {"natalia": "combat", "flint": "combat", "cloris": "gatherer"}


def test_build_roster_includes_only_owned_tags_role_and_free():
    entries = {
        "natalia": {"available": True, "level": 60},
        "flint": {"available": True, "level": 40},
        "cloris": {"available": True, "level": 30},
        "zinman": {"available": False, "shards_current": 3, "shards_required": 10},  # locked
        "broken": "not-a-dict",
    }
    roster = build_roster(entries, ROLE_OF)
    assert roster == [
        {"id": "flint", "role": "combat", "free": True},
        {"id": "natalia", "role": "combat", "free": True},
        {"id": "cloris", "role": "gatherer", "free": True},
    ]  # owned only, sorted by (role, id), zinman/broken excluded


def test_build_roster_defaults_unknown_role_to_combat():
    roster = build_roster({"mystery": {"available": True}}, role_of={})
    assert roster == [{"id": "mystery", "role": "combat", "free": True}]


def test_build_roster_empty_when_nothing_owned():
    assert build_roster({"zinman": {"available": False}}, ROLE_OF) == []


def test_roundtrip_into_resource_allocator_parse():
    # The exact contract: build_roster → JSON → _parse_hero_roster → {role: [ids]}.
    entries = {
        "natalia": {"available": True},
        "flint": {"available": True},
        "cloris": {"available": True},
    }
    payload = json.dumps(build_roster(entries, ROLE_OF))
    by_role = _parse_hero_roster({"heroes.roster": payload})
    assert by_role == {"combat": ["flint", "natalia"], "gatherer": ["cloris"]}
