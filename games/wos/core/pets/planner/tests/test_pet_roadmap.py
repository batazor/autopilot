"""Tests for the pet roadmap (advancement score → SvS pts + at-max stats)."""
from __future__ import annotations

from games.wos.core.pets.planner import PetSpec, load_pet_catalog, pet_roadmap


def _spec(pet_id: str, rarity: str, max_level: int, atk=0.0, dfn=0.0, refine=0.0) -> PetSpec:
    return PetSpec(
        id=pet_id, name=pet_id, rarity=rarity, unlock_days=None, prereq=None,
        skill_name="", category="combat",
        troop_attack_pct=atk, troop_defense_pct=dfn, max_refinement_pct=refine,
        max_level=max_level,
    )


def _catalog():
    return {
        "common_pet": _spec("common_pet", "", 50, atk=10.0, dfn=10.0, refine=13.0),
        "ssr_pet": _spec("ssr_pet", "SSR", 100, atk=31.46, dfn=31.46, refine=44.69),
    }


def test_advancement_counting_and_partial_target() -> None:
    cat = _catalog()
    # 0 → 30 = 3 advancements; not at max → no stat totals.
    rm = pet_roadmap(cat, {"common_pet": 0}, {"common_pet": 30})
    assert rm.advancements == 3
    assert rm.advancement_score == 3
    assert rm.svs_points == 3 * 50
    assert rm.troop_attack_pct == 0.0 and rm.refinement_pct == 0.0


def test_clamp_to_max_and_at_max_stats() -> None:
    cat = _catalog()
    # 25 → 55 clamps to 50: floor(50/10) - floor(25/10) = 5 - 2 = 3 advancements; at max → stats.
    rm = pet_roadmap(cat, {"common_pet": 25}, {"common_pet": 55})
    assert rm.advancements == 3
    assert rm.troop_attack_pct == 10.0
    assert rm.troop_defense_pct == 10.0
    assert rm.refinement_pct == 13.0
    assert rm.per_pet[0]["at_max"] is True
    assert rm.per_pet[0]["to_level"] == 50


def test_multiple_pets_and_svs_points() -> None:
    cat = _catalog()
    rm = pet_roadmap(cat, {}, {"common_pet": 50, "ssr_pet": 100})
    assert rm.advancements == 5 + 10            # common 0→50, ssr 0→100
    assert rm.svs_points == 15 * 50
    assert rm.troop_attack_pct == round(10.0 + 31.46, 2)


def test_missing_pet_reported() -> None:
    rm = pet_roadmap(_catalog(), {}, {"nope": 50})
    assert rm.missing == ("nope",)
    assert rm.advancements == 0 and rm.svs_points == 0


def test_real_catalog_max_levels_from_data() -> None:
    # max_level now comes from advancement_costs.yaml (per-pet), NOT rarity:
    cat = load_pet_catalog()
    assert cat["cave_hyena"].max_level == 50
    assert cat["musk_ox"].max_level == 60          # was wrongly 50 under the rarity rule
    assert cat["snow_leopard"].max_level == 80     # common, but caps at 80 not 50
    assert cat["cave_lion"].max_level == 100        # SSR


def test_real_catalog_material_totals() -> None:
    cat = load_pet_catalog()
    # SSR 0→100: full tier-100 table summed (verbatim from the wiki).
    rm = pet_roadmap(cat, {"cave_lion": 0}, {"cave_lion": 100})
    assert rm.advancements == 10
    assert rm.materials["taming_manual"] == 2990      # 35+70+110+145+220+290+365+440+585+730
    assert rm.materials["energizing_potion"] == 600   # 15+35+50+65+85+100+115+135
    assert rm.materials["strengthening_serum"] == 310 # 10+20+40+60+80+100
    assert rm.troop_attack_pct > 0

    # Common cave_hyena 0→50 (tier-50 table).
    rm2 = pet_roadmap(cat, {"cave_hyena": 0}, {"cave_hyena": 50})
    assert rm2.advancements == 5
    assert rm2.materials["taming_manual"] == 240      # 15+30+45+60+90
    assert rm2.materials["energizing_potion"] == 60   # 10+20+30
    assert rm2.materials["strengthening_serum"] == 10

    # Partial range only counts milestones strictly above current.
    rm3 = pet_roadmap(cat, {"cave_lion": 50}, {"cave_lion": 70})
    assert rm3.advancements == 2                      # milestones 60, 70
    assert rm3.materials["taming_manual"] == 290 + 365
