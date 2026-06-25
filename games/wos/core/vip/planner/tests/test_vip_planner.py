"""VIP planner — ladder data, next-step pick, gating, affordability, roadmap."""
from games.wos.core.roles import get_role
from games.wos.core.vip.planner import (
    INSUFFICIENT_RESOURCES,
    NONE,
    SELECTED,
    load_vip_levels,
    plan_next,
    vip_roadmap,
)

RICH = {"vip_points": 10**9}


# --- data ----------------------------------------------------------------- #
def test_real_table_loads():
    d = load_vip_levels()
    assert d.max_level == 12
    assert len(d.levels) == 12
    assert d.point_items == (10, 100, 1000, 10000)
    assert d.unlock_furnace_level == 0
    assert d.cumulative_xp(1) == 0
    assert d.cumulative_xp(12) == 4_810_000


def test_cumulative_matches_xp_to_next_chain():
    d = load_vip_levels()
    for lvl in range(1, 12):
        assert d.cumulative_xp(lvl) + d.xp_to_next(lvl) == d.cumulative_xp(lvl + 1)
    assert d.xp_to_next(12) is None


# --- plan_next ------------------------------------------------------------ #
def test_next_step_is_one_level_up():
    plan = plan_next(1, 0, RICH)
    assert plan.reason == SELECTED
    assert plan.step is not None
    assert plan.step.to_level == 2
    assert plan.step.xp_needed == 2500
    assert plan.step.cost == {"vip_points": 2500}


def test_current_xp_reduces_needed():
    plan = plan_next(1, 1000, RICH)
    assert plan.step.xp_needed == 1500          # 2500 - 1000 already banked


def test_unknown_level_clamps_to_base():
    # vip.level defaults to 0 before the reader runs → treated as the VIP-1 base.
    assert plan_next(0, 0, RICH).step.to_level == 2


def test_maxed_returns_none():
    assert plan_next(12, 0, RICH).reason == NONE
    assert plan_next(12, 0, RICH).step is None


def test_target_cap_stops_early():
    assert plan_next(5, 0, RICH, target_level=5).reason == NONE
    assert plan_next(4, 0, RICH, target_level=5).step.to_level == 5


def test_insufficient_points():
    plan = plan_next(8, 0, {"vip_points": 1000})   # VIP 8→9 costs 350k
    assert plan.reason == INSUFFICIENT_RESOURCES
    assert plan.step is None
    assert plan.candidates and plan.candidates[0].xp_needed == 350_000


def test_role_only_tilts_value_not_choice():
    farm = plan_next(1, 0, RICH, role=get_role("farm"))
    fighter = plan_next(1, 0, RICH, role=get_role("fighter"))
    # growth category is universal → both still pick VIP 2; value stays positive.
    assert farm.step.to_level == fighter.step.to_level == 2
    assert farm.step.value > 0


# --- roadmap -------------------------------------------------------------- #
def test_roadmap_full_climb():
    rm = vip_roadmap(1, 0, 12)
    assert rm.total_xp == 4_810_000
    assert rm.cost == {"vip_points": 4_810_000}
    assert rm.steps == 11
    # 4,810,000 is a clean multiple of 10,000.
    assert rm.item_plan == {10000: 481}
    assert rm.leftover_xp == 0


def test_roadmap_item_plan_sums_to_total():
    rm = vip_roadmap(3, 0, 9)
    decomposed = sum(d * n for d, n in rm.item_plan.items()) + rm.leftover_xp
    assert decomposed == rm.total_xp


def test_roadmap_leftover_for_non_round_total():
    # current_xp shaves the total off a round boundary → exercises sub-10 remainder.
    rm = vip_roadmap(1, 5, 2)        # need 2500 - 5 = 2495
    assert rm.total_xp == 2495
    assert rm.item_plan == {1000: 2, 100: 4, 10: 9}
    assert rm.leftover_xp == 5


def test_roadmap_per_level_breakdown():
    rm = vip_roadmap(1, 0, 4)
    assert rm.per_level == (
        {"to_level": 2, "xp": 2500},
        {"to_level": 3, "xp": 5000},
        {"to_level": 4, "xp": 12500},
    )
