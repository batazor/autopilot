"""Hero skill economy buffs: parsing + their effect on skill-investment value."""
from __future__ import annotations

from games.wos.core.roles import get_role
from games.wos.heroes.heroes.planner import (
    UPGRADE_SKILL,
    HeroSkill,
    HeroSpec,
    active_city_buffs,
    economy_buff_uplift,
    load_hero_catalog,
    parse_economy_skills,
    plan_next,
)


def _hero(hid, rarity, sub_class, economy_skills=(), shard_tiers=(5,)):
    return HeroSpec(hid, hid.title(), rarity, "Infantry", sub_class, shard_tiers, economy_skills)


CONSTRUCTION = HeroSkill("Bastionist", "construction", (3.0, 6.0, 9.0, 12.0, 15.0))
GATHER = HeroSkill("Predator", "gather", (5.0, 10.0, 15.0, 20.0, 25.0))


# --- parsing ------------------------------------------------------------------
def test_parse_picks_buff_phrase_not_flavour_text():
    # Two %-lists: consumption reduction (flavour) + building speed (the real buff).
    skills = [{"name": "Bastionist",
               "description": "control of the construction workflow reduces basic resource "
                              "consumption by 1%/2%/3%/4%/5% and increases Building Upgrade "
                              "speed by 3%/6%/9%/12%/15%"}]
    parsed = parse_economy_skills(skills)
    assert len(parsed) == 1
    assert parsed[0].category == "construction"
    assert parsed[0].levels == (3.0, 6.0, 9.0, 12.0, 15.0)   # building speed, not consumption


def test_parse_gather_pct_before_keyword():
    skills = [{"name": "Predator",
               "description": "+ 5%/10%/15%/20%/25% Meat Gathering Speed on the map"}]
    parsed = parse_economy_skills(skills)
    assert parsed[0].category == "gather"
    assert parsed[0].levels[-1] == 25.0


# --- HeroSkill curve ----------------------------------------------------------
def test_skill_buff_curve_marginal_and_remaining():
    assert CONSTRUCTION.buff_at(0) == 0.0
    assert CONSTRUCTION.buff_at(2) == 6.0
    assert CONSTRUCTION.buff_at(99) == 15.0            # clamps to the max level
    assert CONSTRUCTION.marginal(2) == 3.0             # 9 - 6
    assert CONSTRUCTION.remaining(2) == 9.0            # 15 - 6


# --- valuation ----------------------------------------------------------------
def test_uplift_marginal_vs_remaining_and_role_bias():
    spec = _hero("zinman", "Epic", "Growth", (CONSTRUCTION,))
    farm, fighter = get_role("farm"), get_role("fighter")
    # marginal (next level) < remaining (full unrealised potential) at skill 0
    assert economy_buff_uplift(spec, 0, None, marginal=True) \
        < economy_buff_uplift(spec, 0, None, marginal=False)
    # construction is an economy buff → a farm values it above a fighter
    assert economy_buff_uplift(spec, 0, farm, marginal=True) \
        > economy_buff_uplift(spec, 0, fighter, marginal=True)
    # no economy skills → no uplift
    assert economy_buff_uplift(_hero("x", "Epic", "Combat"), 0, farm, marginal=True) == 0.0


# --- planner integration ------------------------------------------------------
def test_skill_upgrade_lifted_for_economy_hero():
    plain = _hero("plain", "Epic", "Growth")
    buffed = _hero("buffed", "Epic", "Growth", (CONSTRUCTION,))
    cat = {"plain": plain, "buffed": buffed}
    res = {"book:epic": 999}                            # only skill books → compare skill steps
    plan = plan_next(cat, {}, res, role=get_role("farm"))
    assert plan.step.hero_id == "buffed"               # equal base, buff tips it
    assert plan.step.kind == UPGRADE_SKILL
    assert "construction" in plan.step.detail


# --- aggregator + real catalog ------------------------------------------------
def test_active_city_buffs_sums_owned_levels():
    cat = {"zinman": _hero("zinman", "Epic", "Growth", (CONSTRUCTION,)),
           "cloris": _hero("cloris", "Rare", "Growth", (GATHER,))}
    owned = {"zinman": {"skill": 3}, "cloris": {"skill": 5}}
    buffs = active_city_buffs(cat, owned)
    assert buffs["construction"] == 9.0                # level 3 → 9%
    assert buffs["gather"] == 25.0                     # level 5 → 25%


def test_real_catalog_parses_known_economy_heroes():
    cat = load_hero_catalog()
    zin = cat.get("zinman")
    assert zin is not None
    assert any(sk.category == "construction" for sk in zin.economy_skills)
    assert any(sk.category == "gather" for sk in cat["cloris"].economy_skills)
