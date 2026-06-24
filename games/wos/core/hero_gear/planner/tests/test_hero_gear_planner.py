"""Hero Gear multi-track planner — data, even-leveling across tracks, gating, roadmap."""
from __future__ import annotations

from games.wos.core.hero_gear.planner import (
    INSUFFICIENT_RESOURCES,
    LOCKED,
    SELECTED,
    hero_gear_roadmap,
    hero_gear_value,
    load_hero_gear_data,
    plan_next,
)
from games.wos.core.roles import get_role

RICH = {"enhancement_xp": 10**7, "essence_stones": 10**6, "weapon_widget": 10**6}


# --- data ---------------------------------------------------------------------
def test_real_tracks_load():
    d = load_hero_gear_data()
    assert len(d.pieces) == 6
    assert set(d.tracks) == {"enhance", "mastery", "widget"}
    assert d.tracks["enhance"].max_level == 100
    assert d.tracks["enhance"].cost_at(1) == 10 and d.tracks["enhance"].cost_at(100) == 2400
    assert d.tracks["mastery"].cost_at(1) == 10 and d.tracks["mastery"].cost_at(20) == 200
    assert d.tracks["widget"].cost_at(10) == 50
    assert d.tracks["mastery"].resource == "essence_stones"


# --- plan_next ----------------------------------------------------------------
def test_per_track_furnace_gates():
    assert plan_next({}, RICH, furnace_level=10).reason == LOCKED        # nothing < F15
    assert plan_next({}, RICH, furnace_level=15).step.track == "enhance"  # only enhance @ F15
    assert plan_next({}, RICH, furnace_level=20).reason == SELECTED       # all unlocked


def test_enhance_track_leads_then_even_levels():
    # Highest track weight + a fresh ladder → enhance L1 is the first pick.
    plan = plan_next({}, RICH)
    assert plan.step.track == "enhance"
    assert plan.step.to_level == 1
    assert plan.step.cost == {"enhancement_xp": 10}


def test_picks_the_affordable_track_when_others_are_short():
    # Only essence stones on hand → enhance/widget are unaffordable, mastery wins.
    plan = plan_next({}, {"essence_stones": 10**6}, furnace_level=20)
    assert plan.reason == SELECTED
    assert plan.step.track == "mastery"


def test_insufficient_when_nothing_affordable():
    plan = plan_next({}, {}, furnace_level=20)
    assert plan.reason == INSUFFICIENT_RESOURCES
    assert plan.step is None
    assert plan.candidates


def test_role_tilts_value_toward_a_fighter():
    fighter, farm = get_role("fighter"), get_role("farm")
    assert hero_gear_value("infantry", "enhance", 1, max_level=100, role=fighter) \
        > hero_gear_value("infantry", "enhance", 1, max_level=100, role=farm)


# --- roadmap (the calculator's development view) ------------------------------
def test_roadmap_totals_per_resource():
    rm = hero_gear_roadmap({}, {"mastery": 20, "widget": 10})
    assert rm.steps == 6 * (20 + 10)                       # 6 pieces × (20 + 10) steps
    assert rm.cost["essence_stones"] == 6 * 2100           # 6 × sum(10..200 step 10)
    assert rm.cost["weapon_widget"] == 6 * 275             # 6 × sum(5..50 step 5)
    assert "enhancement_xp" not in rm.cost                 # enhance not in targets → skipped
