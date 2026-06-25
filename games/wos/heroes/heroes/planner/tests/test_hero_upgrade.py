"""Hero level/EXP: the XP ladder, the LEVEL_UP planner step, and the upgrade roadmap."""
from __future__ import annotations

from games.wos.heroes.heroes.planner import (
    LEVEL_UP,
    HeroSpec,
    hero_upgrade_roadmap,
    level_cost,
    level_furnace_gate,
    load_hero_xp,
    plan_next,
)

HERO = HeroSpec("nat", "Nat", "Rare", "Infantry", "Combat", (10, 40, 115, 300, 600))
CAT = {"nat": HERO}
RICH = {"shard:nat": 999, "book:rare": 999, "hero_xp": 10**9}


# --- XP ladder ----------------------------------------------------------------
def test_hero_xp_loads():
    t = load_hero_xp()
    assert t[2].xp == 480 and t[80].xp == 2_400_000
    assert t[1].furnace == 4 and t[21].furnace == 10 and t[80].furnace == 26


def test_level_cost_sums_the_range():
    assert level_cost(10, 12) == 3800 + 4200       # xp[11] + xp[12]
    assert level_cost(5, 5) == 0                    # not advancing
    assert level_furnace_gate(21) == 10


# --- LEVEL_UP planner step ----------------------------------------------------
def test_level_up_appears_only_with_furnace_set():
    owned = {"nat": {"level": 5, "star": 0, "skill": 0}}
    base = plan_next(CAT, owned, RICH)                              # no furnace → unchanged
    assert all(c.kind != LEVEL_UP for c in base.candidates)
    withf = plan_next(CAT, owned, RICH, furnace_level=10)
    level_ups = [c for c in withf.candidates if c.kind == LEVEL_UP]
    assert level_ups and level_ups[0].cost == {"hero_xp": 1500}     # level 5→6 = xp[6]


def test_level_up_is_gated_by_the_furnace_band():
    owned = {"nat": {"level": 20}}                                  # next level 21 needs Furnace 10
    assert all(c.kind != LEVEL_UP for c in plan_next(CAT, owned, RICH, furnace_level=5).candidates)
    assert any(c.kind == LEVEL_UP for c in plan_next(CAT, owned, RICH, furnace_level=10).candidates)


def test_level_up_flows_through_the_hero_adapter():
    from games.wos.core.coordinator import HERO, from_hero_plan
    owned = {"nat": {"level": 5, "star": 0, "skill": 0}}
    plan = plan_next(CAT, owned, {"hero_xp": 10**6}, furnace_level=10)   # only XP affordable
    assert plan.step.kind == LEVEL_UP
    actions = from_hero_plan(plan)                                       # the full ranked set now
    assert actions and all(a.channel_kind == HERO for a in actions)
    level_up = next(a for a in actions if a.key.endswith(f":{LEVEL_UP}"))
    assert level_up.cost == {"hero_xp": 1500}                           # level 5→6 = xp[6]


# --- roadmap (the calculator's headline) --------------------------------------
def test_roadmap_totals_level_star_skill():
    rm = hero_upgrade_roadmap(HERO, {"level": 1, "star": 0, "skill": 0},
                              {"level": 3, "star": 2, "skill": 1})
    assert rm.cost["hero_xp"] == 480 + 690         # level_cost(1, 3)
    assert rm.cost["shard:nat"] == 10 + 40         # shard_cost(0) + shard_cost(1)
    assert rm.cost["book:rare"] == 4               # 1 skill × Rare book cost
    assert rm.steps == 2 + 2 + 1


def test_roadmap_skips_non_advancing_dims():
    rm = hero_upgrade_roadmap(HERO, {"level": 5, "star": 2, "skill": 3},
                              {"level": 5, "star": 2, "skill": 3})
    assert rm.cost == {} and rm.steps == 0
