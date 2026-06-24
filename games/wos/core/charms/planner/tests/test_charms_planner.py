"""Chief Charms planner — data, even-leveling pick, gating, affordability, roadmap."""
from __future__ import annotations

from games.wos.core.charms.planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    SELECTED,
    charm_roadmap,
    charm_value,
    load_charm_data,
    plan_next,
)
from games.wos.core.roles import get_role

RICH = {"charm_guide": 10**6, "charm_design": 10**6, "charm_secrets": 10**6}


# --- data ---------------------------------------------------------------------
def test_real_table_loads():
    d = load_charm_data()
    assert len(d.slots) == 18                       # 6 pieces × 3 charms
    assert d.unlock_furnace_level == 25
    assert d.max_level == 16
    assert d.level(1).cost == {"charm_guide": 5}
    assert d.level(16).cost == {"charm_guide": 650, "charm_design": 550, "charm_secrets": 100}
    assert d.level(12).power is None                # source lacks power above L11


# --- plan_next ----------------------------------------------------------------
def test_locked_below_furnace_25():
    assert plan_next({}, RICH, furnace_level=20).reason == LOCKED
    assert plan_next({}, RICH, furnace_level=25).reason == SELECTED


def test_even_levels_the_lagging_slots_first():
    # One slot raced ahead; the planner raises a level-0 slot (to L1) before it.
    plan = plan_next({"infantry_1": 5}, RICH)
    assert plan.reason == SELECTED
    assert plan.step.to_level == 1                  # a lagging (level-0) slot
    assert plan.step.slot_id != "infantry_1"
    assert plan.step.cost == {"charm_guide": 5}


def test_insufficient_when_a_material_is_short():
    # Every slot at L1 → the next step (L2) needs charm_design, which is 0.
    owned = dict.fromkeys(load_charm_data().slots, 1)
    plan = plan_next(owned, {"charm_guide": 10**6}, furnace_level=25)
    assert plan.reason == INSUFFICIENT_RESOURCES
    assert plan.step is None
    assert plan.candidates                          # still ranked for the trace


def test_role_tilts_value_toward_a_fighter():
    fighter, farm = get_role("fighter"), get_role("farm")
    assert charm_value("infantry", 1, max_level=16, role=fighter) \
        > charm_value("infantry", 1, max_level=16, role=farm)


# --- roadmap (the calculator's development view) ------------------------------
def test_roadmap_totals_across_all_slots():
    # From scratch to L2: each of 18 slots does L1 (5 guide) + L2 (40 guide, 15 design).
    rm = charm_roadmap({}, 2)
    assert rm.steps == 36                           # 18 slots × 2 levels
    assert rm.cost["charm_guide"] == 18 * (5 + 40)
    assert rm.cost["charm_design"] == 18 * 15


def test_roadmap_to_max_includes_secrets():
    rm = charm_roadmap({}, 16)
    assert rm.cost["charm_secrets"] == 18 * (15 + 30 + 45 + 70 + 100)   # L12-16 secrets
