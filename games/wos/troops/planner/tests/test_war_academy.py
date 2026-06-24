"""War Academy → trainable troop tier bridge (Helios research unlocks T11)."""
from __future__ import annotations

from games.wos.troops.planner import BASE_TIER, HELIOS_TIER, unlocked_max_tier


def test_no_research_caps_every_camp_at_base_tier():
    assert unlocked_max_tier({}) == {"infantry": 10, "lancer": 10, "marksman": 10}
    assert BASE_TIER == 10


def test_helios_research_unlocks_t11_for_that_type_only():
    caps = unlocked_max_tier({"helios_infantry": 1})
    assert caps["infantry"] == HELIOS_TIER       # 11 — Helios researched
    assert caps["lancer"] == BASE_TIER           # 10 — not yet
    assert caps["marksman"] == BASE_TIER


def test_none_levels_is_base():
    assert unlocked_max_tier()["marksman"] == BASE_TIER
