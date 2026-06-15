"""Multi-track value-greedy build slate: queues, affordability, role, shelters."""
from __future__ import annotations

from games.wos.core.building.planner import (
    ALL_MAXED,
    INSUFFICIENT_RESOURCES,
    SELECTED,
    BuildGraph,
    BuildingSpec,
    LevelReq,
    plan_builds,
)
from games.wos.core.roles import get_role


def _lvl(level, rank, prereqs=(), cost=()):
    return LevelReq(level, rank, prereqs, cost, 0, None)


def _graph():
    furnace = BuildingSpec("furnace", "Furnace", (
        _lvl("1", 1.0),
        _lvl("2", 2.0, (), (("big", 1000),)),     # expensive next step
    ))
    sawmill = BuildingSpec("sawmill", "Sawmill", (
        _lvl("1", 1.0, (("furnace", 1.0),), (("w", 10),)),
        _lvl("2", 2.0, (("furnace", 2.0),), (("w", 20),)),
    ))
    shelter = BuildingSpec("shelter", "Shelter", (
        _lvl("1", 1.0, (("furnace", 1.0),), (("w", 5),)),
        _lvl("2", 2.0, (("furnace", 2.0),), (("w", 8),)),
        _lvl("3", 3.0, (("furnace", 3.0),), (("w", 12),)),
    ))
    camp = BuildingSpec("infantry_camp", "Infantry Camp", (
        _lvl("1", 1.0, (("furnace", 1.0),), (("w", 10),)),
    ))
    return BuildGraph(buildings={b.id: b for b in (furnace, sawmill, shelter, camp)})


RICH = {"w": 100_000, "big": 100_000}
G = _graph()


def test_fills_both_queues_progression_first():
    slate = plan_builds(G, {"furnace": 1}, free_queues=2, resources=RICH)
    assert slate.reason == SELECTED
    assert len(slate.picks) == 2
    assert slate.picks[0].track == "progression"     # value 100 wins queue 1
    assert slate.picks[0].instance_id == "furnace"
    assert slate.picks[1].instance_id != "furnace"    # a distinct second build


def test_balanced_second_pick_is_camp_over_producer():
    # balanced: camp 60 > producer 55 > shelter 50.
    slate = plan_builds(G, {"furnace": 1}, free_queues=2, resources=RICH)
    assert slate.picks[1].instance_id == "infantry_camp"


def test_farm_prefers_producer_fighter_prefers_camp():
    farm = plan_builds(G, {"furnace": 1}, free_queues=2, resources=RICH, role=get_role("farm"))
    assert farm.picks[1].spec_id == "sawmill"         # camp down-weighted
    fighter = plan_builds(G, {"furnace": 1}, free_queues=2, resources=RICH, role=get_role("fighter"))
    assert fighter.picks[1].spec_id == "infantry_camp"


def test_unaffordable_furnace_falls_back_to_economy():
    # No "big" → furnace L2 unaffordable; economy (cost "w") fills both queues.
    slate = plan_builds(G, {"furnace": 1}, free_queues=2, resources={"w": 100_000})
    assert slate.reason == SELECTED
    assert all(p.instance_id != "furnace" for p in slate.picks)
    assert all(p.affordable for p in slate.picks)


def test_eight_shelter_instances_are_tracked():
    slate = plan_builds(G, {"furnace": 1}, free_queues=2, resources=RICH)
    shelters = [c for c in slate.candidates if c.spec_id == "shelter"]
    assert len(shelters) == 8                          # one candidate per plot


def test_lifts_the_lagging_shelter_first():
    # shelter_1 already at 2; the level-0 plots (cheaper next step) outrank it.
    levels = {"furnace": 3, "shelter_1": 2}
    slate = plan_builds(G, levels, free_queues=8, resources=RICH, role=get_role("farm"))
    shelter_picks = [p for p in slate.picks if p.spec_id == "shelter"]
    assert shelter_picks
    assert shelter_picks[0].instance_id != "shelter_1"   # a lagging plot goes first


def test_insufficient_resources_when_nothing_affordable():
    slate = plan_builds(G, {"furnace": 1}, free_queues=2, resources={})
    assert slate.reason == INSUFFICIENT_RESOURCES
    assert slate.picks == ()
    assert slate.candidates                              # ready candidates still listed


def test_free_queues_capped():
    slate = plan_builds(G, {"furnace": 1}, free_queues=1, resources=RICH)
    assert len(slate.picks) == 1


def test_all_maxed():
    levels = {"furnace": 2, "sawmill": 2, "infantry_camp": 1}
    levels.update({f"shelter_{i}": 3 for i in range(1, 9)})
    slate = plan_builds(G, levels, free_queues=2, resources=RICH)
    assert slate.reason == ALL_MAXED
    assert slate.picks == ()
