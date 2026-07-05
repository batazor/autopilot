"""Unit tests for the new-hero unlock recorder's pure pieces:

* ``match_hero_id`` — OCR name → canonical hero id (exact / fuzzy / miss).
* ``merge_new_hero_entry`` — unlock merge that preserves prior scan data.
"""
from __future__ import annotations

from games.wos.heroes.heroes.hero_name_match import match_hero_id
from games.wos.heroes.heroes.record_new_hero import merge_new_hero_entry

from config.heroes import HeroDef, HeroRegistry, get_hero_registry

REG = HeroRegistry(
    heroes=(
        HeroDef(id="renee", name="Renee", sub_class="Combat"),
        HeroDef(id="ling_xue", name="Ling Xue", sub_class="Growth"),
        HeroDef(id="ahmose", name="Ahmose", sub_class="Combat"),
    )
)


# ---------------------------------------------------------------- match_hero_id


def test_exact_match_is_score_one():
    assert match_hero_id("Renee", REG) == ("renee", 1.0)


def test_match_ignores_case_and_whitespace():
    assert match_hero_id("  renee ", REG) == ("renee", 1.0)


def test_multiword_name_resolves_to_underscored_id():
    # OCR reads the display name "Ling Xue"; the registry id is "ling_xue".
    assert match_hero_id("Ling Xue", REG) == ("ling_xue", 1.0)


def test_id_shaped_input_still_resolves():
    assert match_hero_id("ling_xue", REG) == ("ling_xue", 1.0)


def test_fuzzy_recovers_single_letter_ocr_slip():
    hero_id, score = match_hero_id("Renae", REG)  # 'a' instead of 'e'
    assert hero_id == "renee"
    assert 0.6 <= score < 1.0


def test_unrelated_text_matches_nothing():
    assert match_hero_id("Zzzqplmx", REG) == (None, 0.0)


def test_empty_input_matches_nothing():
    assert match_hero_id("", REG) == (None, 0.0)
    assert match_hero_id("   ", REG) == (None, 0.0)


def test_renee_resolves_against_the_real_registry():
    # Locks the live behaviour the device verification relied on.
    hero_id, score = match_hero_id("Renee", get_hero_registry())
    assert hero_id == "renee"
    assert score == 1.0


# ------------------------------------------------------- merge_new_hero_entry


def test_merge_into_empty_marks_available_and_stamps():
    entry = merge_new_hero_entry(None, "Renee", now=1000.0)
    assert entry == {
        "name": "Renee",
        "available": True,
        "seen_at": 1000.0,
        "unlocked_at": 1000.0,
        "source": "sr_new_unlock",
    }


def test_merge_preserves_prior_scan_fields_and_flips_available():
    prev = {
        "name": "Renee",
        "available": False,
        "level": 1,
        "shards_current": 8,
        "shards_required": 10,
        "unlocked_at": 500.0,  # already unlocked earlier — keep the first stamp
    }
    entry = merge_new_hero_entry(prev, "Renee", now=2000.0)
    assert entry["available"] is True
    assert entry["level"] == 1
    assert entry["shards_current"] == 8
    assert entry["shards_required"] == 10
    assert entry["unlocked_at"] == 500.0  # setdefault keeps the original
    assert entry["seen_at"] == 2000.0
    assert entry["source"] == "sr_new_unlock"


def test_merge_tolerates_non_dict_prev():
    entry = merge_new_hero_entry("not-a-dict", "Ahmose", now=42.0)
    assert entry["name"] == "Ahmose"
    assert entry["available"] is True
    assert entry["unlocked_at"] == 42.0
