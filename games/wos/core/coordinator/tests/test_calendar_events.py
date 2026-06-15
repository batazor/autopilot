"""Calendar/event bias: boost the rewarded domain now, hoard speedups for windows."""
from __future__ import annotations

from games.wos.core.coordinator import (
    RESEARCH,
    Channel,
    EventWindow,
    calendar_bias,
    coordinate,
    domain_priority,
    from_research_plan,
)
from games.wos.core.coordinator.events import (
    CONSTRUCTION,
    DEFAULT_BOOST,
    PROVISIONAL_BOOST,
)
from games.wos.core.research.planner import load_research_graph, plan_next

DAY = 86_400


def test_active_power_up_boosts_all_power_domains():
    bias = calendar_bias([EventWindow("power_up", active=True, ends_in_s=DAY)])
    # ANY_POWER → construction + economy + research + troops all lifted.
    for d in ("building_progression", "building_economy", "research", "troops"):
        assert bias.domain_boost[d] == DEFAULT_BOOST
    assert "raids" not in bias.domain_boost          # combat doesn't score power-up points
    assert bias.holds and bias.holds[0].until_s == 0.0   # "spend now" hold


def test_phase_category_narrows_the_boost():
    # A construction-themed phase → only building domains, not research.
    bias = calendar_bias([
        EventWindow("armament_competition", active=True, phase_category=CONSTRUCTION, ends_in_s=DAY),
    ])
    assert "building_progression" in bias.domain_boost
    assert "research" not in bias.domain_boost


def test_active_phased_event_unread_is_provisional_and_flags_a_read():
    # Live phased event, theme not read yet → soft guess + "go read it".
    bias = calendar_bias([EventWindow("armament_competition", active=True, ends_in_s=DAY)])
    assert "armament_competition" in bias.needs_read
    assert bias.domain_boost["building_progression"] == PROVISIONAL_BOOST


def test_read_theme_upgrades_to_full_boost_and_clears_read():
    # Once the live theme is read, boost is full and no read is needed.
    bias = calendar_bias([
        EventWindow("armament_competition", active=True, phase_category=CONSTRUCTION, ends_in_s=DAY),
    ])
    assert bias.needs_read == ()
    assert bias.domain_boost["building_progression"] == DEFAULT_BOOST


def test_non_phased_active_event_needs_no_read():
    bias = calendar_bias([EventWindow("power_up", active=True, ends_in_s=DAY)])
    assert bias.needs_read == ()                     # any-power → nothing to read
    assert bias.domain_boost["research"] == DEFAULT_BOOST


def test_upcoming_event_emits_hoard_hold_without_boost():
    bias = calendar_bias([EventWindow("power_up", active=False, starts_in_s=DAY)])
    assert bias.domain_boost == {}                   # not live → no boost yet
    assert len(bias.holds) == 1
    assert bias.holds[0].until_s == DAY              # save speedups for the window


def test_far_future_event_no_hold():
    bias = calendar_bias([EventWindow("power_up", active=False, starts_in_s=10 * DAY)])
    assert bias.holds == ()                          # beyond the 2-day horizon


def test_non_points_event_ignored():
    bias = calendar_bias([EventWindow("bear_hunt", active=True, ends_in_s=DAY)])
    assert bias.domain_boost == {}
    assert bias.holds == ()


def test_boost_lifts_domain_priority():
    base = domain_priority("research")
    boosted = domain_priority("research", boost=DEFAULT_BOOST)
    assert boosted > base
    assert boosted == base * DEFAULT_BOOST


def test_boost_flows_through_adapter_into_coordination():
    g = load_research_graph()
    bias = calendar_bias([EventWindow("power_up", active=True, ends_in_s=DAY)])
    cands = from_research_plan(plan_next(g, {}, rc_level=30), g, boosts=bias.domain_boost)
    assert cands[0].priority == domain_priority("research") * DEFAULT_BOOST
    dec = coordinate([Channel("r1", RESEARCH)], cands, {"meat": 1, "wood": 1, "coal": 1, "iron": 1, "steel": 1})
    # cost may exceed the tiny pool; assert it was at least considered for the lane
    assert dec.commits or dec.starved
