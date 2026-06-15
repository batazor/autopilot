"""Value-greedy hero investment: rarity × role × generation, books/shards budget."""
from __future__ import annotations

from games.wos.core.heroes.planner import (
    INSUFFICIENT_RESOURCES,
    PROMOTE_STAR,
    SELECTED,
    UPGRADE_SKILL,
    HeroSpec,
    generation_factor,
    hero_value,
    load_hero_catalog,
    plan_next,
)
from games.wos.core.roles import get_role


def _hero(hid, rarity, sub_class, shard_tiers=(5,)):
    return HeroSpec(hid, hid.title(), rarity, "Infantry", sub_class, shard_tiers)


COMBAT_LEG = _hero("natalia", "Legendary", "Combat")
GROWTH_RARE = _hero("cloris", "Rare", "Growth")
CATALOG = {"natalia": COMBAT_LEG, "cloris": GROWTH_RARE}
RICH = {"shard:natalia": 999, "shard:cloris": 999, "book:mythic": 999, "book:rare": 999}


# --- policy ------------------------------------------------------------------


def test_rarity_orders_value():
    assert hero_value(COMBAT_LEG) > hero_value(_hero("x", "Epic", "Combat")) > hero_value(GROWTH_RARE)


def test_generation_factor_decays_and_gates():
    assert generation_factor(None, 5) == 1.0          # unknown → current
    assert generation_factor(5, 5) == 1.0
    assert 0 < generation_factor(4, 5) < 1.0          # 1 behind → decayed
    assert generation_factor(1, 5) == 0.0             # ≥4 behind → obsolete


def test_role_tilts_combat_vs_growth():
    fighter, farm = get_role("fighter"), get_role("farm")
    assert hero_value(COMBAT_LEG, role=fighter) > hero_value(COMBAT_LEG, role=farm)
    assert hero_value(GROWTH_RARE, role=farm) > hero_value(GROWTH_RARE, role=fighter)


# --- planner -----------------------------------------------------------------


def test_picks_highest_value_affordable():
    plan = plan_next(CATALOG, {}, RICH)
    assert plan.reason == SELECTED
    assert plan.step.hero_id == "natalia"             # Legendary combat outranks Rare growth
    assert plan.step.kind == PROMOTE_STAR             # star ≥ skill at equal hero


def test_obsolete_generation_is_skipped():
    plan = plan_next(CATALOG, {}, RICH, current_generation=6,
                     hero_generation={"natalia": 1, "cloris": 6})
    assert plan.step.hero_id == "cloris"              # natalia (4+ behind) → value 0, skipped


def test_shard_cost_is_per_hero_and_blocks_when_empty():
    # No natalia shards, but books available → fall back to her skill, or to cloris.
    res = {"shard:cloris": 999, "book:mythic": 999, "book:rare": 999}
    plan = plan_next(CATALOG, {}, res)
    assert plan.step is not None
    # natalia star needs shard:natalia (0) → not chosen; her skill (book:mythic) can be.
    if plan.step.hero_id == "natalia":
        assert plan.step.kind == UPGRADE_SKILL


def test_insufficient_resources_when_nothing_affordable():
    plan = plan_next(CATALOG, {}, {})
    assert plan.reason == INSUFFICIENT_RESOURCES
    assert plan.step is None
    assert plan.candidates                            # still ranked for the trace


def test_role_flips_pick_between_same_rarity_heroes():
    cat = {"ec": _hero("ec", "Epic", "Combat"), "eg": _hero("eg", "Epic", "Growth")}
    res = {"shard:ec": 999, "shard:eg": 999, "book:epic": 999}
    assert plan_next(cat, {}, res, role=get_role("farm")).step.hero_id == "eg"
    assert plan_next(cat, {}, res, role=get_role("fighter")).step.hero_id == "ec"


# --- real catalog ------------------------------------------------------------


def test_real_catalog_loads():
    cat = load_hero_catalog()
    assert len(cat) > 40
    assert "flint" in cat
    assert cat["flint"].sub_class == "Combat"
