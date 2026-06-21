"""Furnace-first planner: parsing helpers + the recursive build-order logic."""
from __future__ import annotations

from games.wos.core.building.planner import (
    BLOCKED,
    GOAL_REACHED,
    GOAL_UNKNOWN,
    SELECTED,
    BuildGraph,
    BuildingSpec,
    LevelReq,
    level_rank,
    load_graph,
    parse_amount,
    parse_duration,
    parse_prerequisites,
    plan_next,
)

NAME_TO_ID = {
    "Furnace": "furnace", "Embassy": "embassy", "Research Center": "research_center",
    "Lancer Camp": "lancer_camp", "Marksman Camp": "marksman_camp", "Infirmary": "infirmary",
}


# --- parsing helpers ---------------------------------------------------------


def test_parse_amount_suffixes():
    assert parse_amount("67M") == 67_000_000
    assert parse_amount("3.3M") == 3_300_000
    assert parse_amount("460k") == 460_000
    assert parse_amount("1.2K") == 1_200
    assert parse_amount("132") == 132
    assert parse_amount(None) == 0


def test_parse_duration_forms():
    assert parse_duration("7d") == 604_800
    assert parse_duration("04:30:00") == 16_200
    assert parse_duration("00:00:02") == 2
    assert parse_duration("33d 11:42:00") == 33 * 86_400 + 11 * 3_600 + 42 * 60


def test_level_rank_orders_fire_crystal_after_30():
    assert level_rank("10") == 10
    assert level_rank(9) == 9
    assert level_rank("FC-8") == 38
    assert level_rank("30-8") == 38
    assert level_rank("FC-1") > level_rank("30")
    assert level_rank(0) == 0


def test_parse_prerequisites_multiword_and_levels():
    assert parse_prerequisites("Embassy Lv. 30, Research Center Lv. 30", NAME_TO_ID) == (
        ("embassy", 30.0), ("research_center", 30.0),
    )
    assert parse_prerequisites("Furnace Lv. 10 Embassy Lv. 3", NAME_TO_ID) == (
        ("furnace", 10.0), ("embassy", 3.0),
    )
    # "Lvl." spelling and a trailing name with no level → rank 1 (must exist).
    assert parse_prerequisites("Marksman Camp Lvl. 9 Research Center", NAME_TO_ID) == (
        ("marksman_camp", 9.0), ("research_center", 1.0),
    )
    assert parse_prerequisites("Furnace FC-8", NAME_TO_ID) == (("furnace", 38.0),)


def test_parse_prerequisites_ignores_unknown_names():
    assert parse_prerequisites("Mystery Hall Lv. 5", NAME_TO_ID) == ()


# --- synthetic-graph furnace-first logic -------------------------------------


def _lvl(level, rank, prereqs=()):
    return LevelReq(level=level, rank=rank, prereqs=prereqs, cost=(), time_s=0, power=None)


def _graph(*specs):
    return BuildGraph(buildings={s.id: s for s in specs})


FURNACE = BuildingSpec("furnace", "Furnace", (
    _lvl("1", 1.0),
    _lvl("2", 2.0, (("embassy", 1.0),)),
    _lvl("3", 3.0, (("embassy", 2.0),)),
))
EMBASSY = BuildingSpec("embassy", "Embassy", (
    _lvl("1", 1.0, (("furnace", 1.0),)),
    _lvl("2", 2.0, (("furnace", 2.0),)),
    _lvl("3", 3.0, (("furnace", 3.0),)),
))
GRAPH = _graph(FURNACE, EMBASSY)


def test_picks_furnace_when_prereqs_met():
    plan = plan_next(GRAPH, {"furnace": 1, "embassy": 1}, goal_cap=3.0)
    assert plan.reason == SELECTED
    assert plan.step.building_id == "furnace"
    assert plan.step.to_rank == 2.0
    assert plan.chain == ("furnace",)


def test_recurses_into_unmet_prerequisite():
    # Furnace 2 needs Embassy 1, but embassy isn't built → build embassy first.
    plan = plan_next(GRAPH, {"furnace": 1, "embassy": 0}, goal_cap=3.0)
    assert plan.reason == SELECTED
    assert plan.step.building_id == "embassy"
    assert plan.step.to_rank == 1.0
    assert plan.chain == ("furnace", "embassy")


def test_recurses_one_level_deep_toward_higher_furnace():
    plan = plan_next(GRAPH, {"furnace": 2, "embassy": 1}, goal_cap=3.0)
    assert plan.step.building_id == "embassy"
    assert plan.step.to_rank == 2.0


def test_goal_reached_at_cap():
    plan = plan_next(GRAPH, {"furnace": 3, "embassy": 3}, goal_cap=3.0)
    assert plan.reason == GOAL_REACHED
    assert plan.step is None


def test_goal_unknown():
    plan = plan_next(GRAPH, {}, goal_id="nonexistent")
    assert plan.reason == GOAL_UNKNOWN


def test_blocked_when_prereq_cannot_advance():
    # Embassy maxes at 1, but Furnace 2 needs Embassy 2 → unsatisfiable.
    embassy_capped = BuildingSpec("embassy", "Embassy", (_lvl("1", 1.0, (("furnace", 1.0),)),))
    furnace_needs2 = BuildingSpec("furnace", "Furnace", (
        _lvl("1", 1.0), _lvl("2", 2.0, (("embassy", 2.0),)),
    ))
    graph = _graph(furnace_needs2, embassy_capped)
    plan = plan_next(graph, {"furnace": 1, "embassy": 1}, goal_cap=2.0)
    assert plan.reason == BLOCKED
    assert plan.step is None


# --- against the real db/buildings graph -------------------------------------


def test_real_graph_loads():
    graph = load_graph()
    assert graph.spec("furnace") is not None
    assert len(graph.spec("furnace").levels) >= 30
    assert graph.spec("command_center") is not None


def test_real_graph_all_maxed_is_goal_reached():
    graph = load_graph()
    levels = dict.fromkeys(graph.buildings, 30)
    assert plan_next(graph, levels).reason == GOAL_REACHED


def test_real_graph_pushes_furnace_when_unblocked():
    graph = load_graph()
    levels = dict.fromkeys(graph.buildings, 30)
    levels["furnace"] = 10                       # everything else is ahead of furnace
    plan = plan_next(graph, levels)
    assert plan.reason == SELECTED
    assert plan.step.building_id == "furnace"
    assert plan.step.to_rank == 11.0


def _costed_furnace():
    return BuildingSpec("furnace", "Furnace", (
        _lvl("1", 1.0),
        LevelReq("2", 2.0, (), (("wood", 100), ("coal", 20)), 0, None),
    ))


def test_affordability_not_checked_without_resources():
    plan = plan_next(GRAPH, {"furnace": 1, "embassy": 1}, goal_cap=3.0)
    assert plan.affordable is True          # default when no balances given
    assert plan.shortfall == ()


def test_affordable_when_resources_sufficient():
    g = _graph(_costed_furnace())
    plan = plan_next(g, {"furnace": 1}, goal_cap=3.0, resources={"wood": 100, "coal": 50})
    assert plan.step.building_id == "furnace"
    assert plan.affordable is True
    assert plan.shortfall == ()


def test_shortfall_when_insufficient():
    g = _graph(_costed_furnace())
    plan = plan_next(g, {"furnace": 1}, goal_cap=3.0, resources={"wood": 40})
    assert plan.step.building_id == "furnace"   # target unchanged — bot gathers/waits
    assert plan.affordable is False
    assert dict(plan.shortfall) == {"wood": 60, "coal": 20}


def test_real_graph_recurses_into_lagging_prerequisite():
    graph = load_graph()
    levels = dict.fromkeys(graph.buildings, 30)
    levels["furnace"] = 10
    levels["embassy"] = 9                         # Furnace 11 needs Embassy 10
    plan = plan_next(graph, levels)
    assert plan.reason == SELECTED
    assert plan.step.building_id == "embassy"
    assert plan.step.to_rank == 10.0
    assert plan.chain[0] == "furnace"
