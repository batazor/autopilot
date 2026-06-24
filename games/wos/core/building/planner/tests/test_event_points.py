"""Event-points scoring: the scorer + its effect on the build slate ranking."""
from __future__ import annotations

from games.wos.core.building.planner import (
    BuildGraph,
    BuildingSpec,
    LevelReq,
    event_weight,
    load_event_scoring,
    plan_builds,
    power_gain,
    upgrade_points,
)
from games.wos.core.building.planner.event_points import level_power_at


def _lvl(level, rank, *, prereqs=(), cost=(), power=None):
    return LevelReq(level, rank, prereqs, cost, 0, power)


# --- Scorer --------------------------------------------------------------------
def test_scoring_table_loads_construction_events():
    scoring = load_event_scoring()
    assert scoring["svs"]["construction"] == 1.0
    assert "any_power" in scoring["power_up"]


def test_event_weight_picks_best_active_construction_event():
    assert event_weight(["svs"]) == 1.0                 # construction window live
    assert event_weight(["power_up"]) == 1.0            # any-power counts for builds
    assert event_weight([]) == 0.0                      # off-window → no bonus
    assert event_weight(["not_an_event"]) == 0.0        # unknown slug ignored


def test_power_gain_uses_level_power_and_carries_null_gaps():
    spec = BuildingSpec("x", "X", (
        _lvl("1", 1.0, power=100),
        _lvl("2", 2.0, power=None),     # null → carries forward the last known power
        _lvl("3", 3.0, power=500),
    ))
    assert level_power_at(spec, 0) == 0                 # unbuilt
    assert level_power_at(spec, 2.0) == 100            # carried from L1
    assert power_gain(spec, 1.0, 3.0) == 400           # 500 - 100
    assert power_gain(spec, 3.0, 1.0) == 0             # never negative


def test_upgrade_points_only_score_inside_a_window():
    assert upgrade_points(100_000, ["svs"]) == 100_000
    assert upgrade_points(100_000, []) == 0             # off-window
    assert upgrade_points(0, ["svs"]) == 0             # no power gain → no points


# --- Effect on the slate ranking ----------------------------------------------
def _ranking_graph():
    """Furnace maxed (no progression noise) + two equal-cost producers that differ
    only in the power their next level grants."""
    furnace = BuildingSpec("furnace", "Furnace", (_lvl("1", 1.0),))
    coal = BuildingSpec("coal_mine", "Coal Mine", (
        _lvl("1", 1.0, prereqs=(("furnace", 1.0),)),
        _lvl("2", 2.0, prereqs=(("furnace", 1.0),), cost=(("coal", 10),), power=10),
    ))
    iron = BuildingSpec("iron_mine", "Iron Mine", (
        _lvl("1", 1.0, prereqs=(("furnace", 1.0),)),
        _lvl("2", 2.0, prereqs=(("furnace", 1.0),), cost=(("iron", 10),), power=500_000),
    ))
    return BuildGraph(buildings={b.id: b for b in (furnace, coal, iron)})


G = _ranking_graph()
LEVELS = {"furnace": 1, "coal_mine": 1, "iron_mine": 1}
RICH = {"coal": 1000, "iron": 1000}


def test_off_window_ranking_is_power_blind():
    slate = plan_builds(G, LEVELS, resources=RICH)        # no active_events
    # Equal value + cost → alphabetical instance_id tiebreak puts coal_mine first.
    assert slate.picks[0].instance_id == "coal_mine"
    assert slate.event_points_total == 0


def test_active_window_front_loads_the_high_power_upgrade():
    slate = plan_builds(G, LEVELS, resources=RICH, active_events=["svs"])
    # iron_mine's large power gain earns an event bonus that lifts it past coal_mine.
    assert slate.picks[0].instance_id == "iron_mine"
    assert slate.picks[0].event_points == 500_000
    assert slate.event_points_total > 0
