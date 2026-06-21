"""Value-greedy pet investment: unlock gate (age + prereq), rarity × role, budget."""
from __future__ import annotations

from games.wos.core.pets.planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    REFINE,
    SELECTED,
    PetSpec,
    categorize_skill,
    is_unlocked,
    load_pet_catalog,
    parse_unlock,
    pet_value,
    plan_next,
)
from games.wos.core.roles import get_role


def _pet(pid, rarity, category, *, unlock_days=None, prereq=None):
    return PetSpec(pid, pid.title(), rarity, unlock_days, prereq, "Skill", category)


SSR_COMBAT = _pet("cave_lion", "SSR", "combat", unlock_days=200, prereq=("snow_leopard", 30))
ORD_GATHER = _pet("giant_elk", "", "gather", unlock_days=140)
CATALOG = {"cave_lion": SSR_COMBAT, "giant_elk": ORD_GATHER}
RICH = {"pet_shard:cave_lion": 99, "pet_shard:giant_elk": 99, "pet_food": 99}


# --- parsing / policy --------------------------------------------------------


def test_parse_unlock_extracts_days_and_prereq():
    assert parse_unlock("Unlock after 200 days and Snow Leopard LV.30") == (200, ("Snow Leopard", 30))
    assert parse_unlock("Unlock after 450Day") == (450, None)


def test_categorize_skill():
    assert categorize_skill({"name": "Lightning Raid", "effect": "March speed"}, None) == "march"
    assert categorize_skill({"name": "Arctic Embrace", "effect": "rejuvenates weary"}, None) == "stamina"
    assert categorize_skill({"name": "Apex Assault", "effect": "Lethality"},
                            {"stat": "Troop Attack"}) == "combat"


def test_rarity_orders_value():
    assert pet_value(SSR_COMBAT) > pet_value(ORD_GATHER)


def test_role_tilts_combat_vs_gather():
    assert pet_value(SSR_COMBAT, role=get_role("fighter")) > pet_value(SSR_COMBAT, role=get_role("farm"))
    assert pet_value(ORD_GATHER, role=get_role("farm")) > pet_value(ORD_GATHER, role=get_role("fighter"))


# --- unlock gate -------------------------------------------------------------


def test_unlock_requires_server_age():
    assert not is_unlocked(ORD_GATHER, 100, {})        # day 100 < 140
    assert is_unlocked(ORD_GATHER, 200, {})


def test_unlock_requires_prerequisite_pet_level():
    assert not is_unlocked(SSR_COMBAT, 300, {})                       # snow_leopard not owned
    assert not is_unlocked(SSR_COMBAT, 300, {"snow_leopard": {"level": 10}})
    assert is_unlocked(SSR_COMBAT, 300, {"snow_leopard": {"level": 30}})


# --- planner -----------------------------------------------------------------


def test_locked_when_server_too_young():
    plan = plan_next(CATALOG, {}, RICH, server_days=50)
    assert plan.reason == LOCKED
    assert plan.step is None


def test_picks_highest_value_unlocked():
    # Day 300 + snow_leopard maxed → both unlocked; SSR combat outranks ordinary gather.
    plan = plan_next(CATALOG, {"snow_leopard": {"level": 30}}, RICH, server_days=300)
    assert plan.reason == SELECTED
    assert plan.step.pet_id == "cave_lion"
    assert plan.step.kind == REFINE


def test_farm_prefers_gather_pet():
    plan = plan_next(CATALOG, {"snow_leopard": {"level": 30}}, RICH, server_days=300,
                     role=get_role("farm"))
    # SSR(100×0.5 combat)=50 vs ordinary(55×1.0 gather)=55 → gather pet wins under farm
    assert plan.step.pet_id == "giant_elk"


def test_insufficient_resources():
    plan = plan_next({"giant_elk": ORD_GATHER}, {}, {}, server_days=200)
    assert plan.reason == INSUFFICIENT_RESOURCES
    assert plan.step is None


def test_real_catalog_loads_and_gates():
    cat = load_pet_catalog()
    assert len(cat) >= 10
    assert "snow_leopard" in cat
    # snow_leopard's skill is March speed → march category
    assert cat["snow_leopard"].category == "march"
    assert cat["snow_leopard"].unlock_days == 140
