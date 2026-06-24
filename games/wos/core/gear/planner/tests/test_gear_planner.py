"""Chief Gear planner — data, even-leveling pick, gating, affordability, roadmap."""
from __future__ import annotations

from games.wos.core.gear.planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    SELECTED,
    gear_roadmap,
    gear_value,
    load_gear_data,
    plan_next,
)
from games.wos.core.roles import get_role

RICH = {
    "hardened_alloy": 10**7, "polishing_solution": 10**6,
    "design_plans": 10**6, "lunar_amber": 10**6,
}


# --- data ---------------------------------------------------------------------
def test_real_ladder_loads():
    d = load_gear_data()
    assert len(d.slots) == 6                         # 6 gear pieces
    assert d.unlock_furnace_level == 22
    assert d.max_level == 42
    assert d.level(1).label == "green_0"
    assert d.level(1).cost == {"hardened_alloy": 1500, "polishing_solution": 15}
    top = d.level(42)
    assert top.label == "pink_t3_4"
    assert top.cost["lunar_amber"] == 25             # Lunar Amber on the top steps
    assert top.power == 3_672_000                    # power present for the whole ladder


# --- plan_next ----------------------------------------------------------------
def test_locked_below_furnace_22():
    assert plan_next({}, RICH, furnace_level=20).reason == LOCKED
    assert plan_next({}, RICH, furnace_level=22).reason == SELECTED


def test_even_levels_the_lagging_piece_first():
    plan = plan_next({"gloves_belt_infantry": 5}, RICH)
    assert plan.reason == SELECTED
    assert plan.step.to_level == 1                   # a lagging (level-0) piece
    assert plan.step.slot_id != "gloves_belt_infantry"
    assert plan.step.label == "green_0"


def test_insufficient_when_a_material_is_short():
    # Every piece at green_0 (level 1) → the next step needs more hardened_alloy.
    owned = dict.fromkeys(load_gear_data().slots, 1)
    plan = plan_next(owned, {"hardened_alloy": 100}, furnace_level=22)   # far too little
    assert plan.reason == INSUFFICIENT_RESOURCES
    assert plan.step is None
    assert plan.candidates


def test_role_tilts_value_toward_a_fighter():
    fighter, farm = get_role("fighter"), get_role("farm")
    assert gear_value("infantry", 1, max_level=42, role=fighter) \
        > gear_value("infantry", 1, max_level=42, role=farm)


# --- roadmap (the calculator's development view) ------------------------------
def test_roadmap_totals_across_all_pieces():
    # From scratch to green_1 (level 2): each of 6 pieces does L1 + L2.
    rm = gear_roadmap({}, 2)
    assert rm.steps == 12                            # 6 pieces × 2 steps
    assert rm.cost["hardened_alloy"] == 6 * (1500 + 3800)
    assert rm.cost["polishing_solution"] == 6 * (15 + 40)
    assert rm.power_gain == 6 * 306_000             # power at green_1 (from level 0)


def test_roadmap_to_top_includes_lunar_amber():
    rm = gear_roadmap({}, 42)
    # Lunar Amber enters at pink_0 (level 27) → 10,10,10,10,15,15,15,15,20,20,20,20,25,25,25,25.
    per_piece_amber = 10 * 4 + 15 * 4 + 20 * 4 + 25 * 4
    assert rm.cost["lunar_amber"] == 6 * per_piece_amber
