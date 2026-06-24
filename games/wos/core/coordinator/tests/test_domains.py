"""The domain registry is the single source of truth — bands, channels, surfacing."""
from __future__ import annotations

from pathlib import Path

import yaml
from games.wos.core.coordinator import (
    DOMAINS,
    channel_kinds,
    dev_channels,
    investment_domain_names,
)
from games.wos.core.coordinator.objective import DOMAIN_BAND, DOMAIN_CATEGORY

# games/wos/core/coordinator/tests/ → parents[3] = games/wos
PLANNERS_YAML = Path(__file__).resolve().parents[3] / "planners.yaml"


def test_bands_and_categories_are_derived_unchanged():
    # Snapshot of the bands the registry must reproduce (the pre-refactor values).
    assert DOMAIN_BAND["research"] == 900.0
    assert DOMAIN_BAND["gear"] == 555.0
    assert DOMAIN_BAND["charms"] == 550.0
    assert DOMAIN_BAND["hero_gear"] == 545.0
    assert DOMAIN_BAND["troops"] == 540.0
    assert {d.name for d in DOMAINS} == set(DOMAIN_BAND) == set(DOMAIN_CATEGORY)
    # the investment planners' role tilt is baked into their value → growth here.
    for name in ("charms", "gear", "hero_gear", "heroes", "pets"):
        assert DOMAIN_CATEGORY[name] == "growth"


def test_channel_kinds_include_the_new_domains():
    kinds = set(channel_kinds())
    assert {"construction", "research", "march", "training", "hero", "pet"} <= kinds
    assert {"charm", "gear", "hero_gear"} <= kinds          # the /meta bug, now fixed


def test_dev_channels_cover_every_investment_lane():
    ids = {cid for cid, _ in dev_channels()}
    assert {"charm_1", "gear_1", "hero_gear_1", "training_1", "hero_1", "pet_1", "research_1"} <= ids


def test_planners_yaml_registers_every_investment_domain():
    doc = yaml.safe_load(PLANNERS_YAML.read_text(encoding="utf-8")) or {}
    registered = {str(p["name"]) for p in (doc.get("planners") or [])}
    assert {"charms", "gear", "hero_gear", "hero_upgrade"} <= registered   # botctl planners lists them
    # every domain with its own dev lane is surfaced in the registry.
    assert set(investment_domain_names()) <= registered
