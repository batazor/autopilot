"""Item-icon costs decode to named resources, enabling per-resource bottleneck repair."""
from __future__ import annotations

from games.wos.core.building.planner import (
    BuildGraph,
    BuildingSpec,
    LevelReq,
    load_graph,
    plan_builds,
    resource_name,
)
from games.wos.core.building.planner.model import _decode_cost


def _lvl(level, rank, *, prereqs=(), cost=()):
    return LevelReq(level, rank, prereqs, cost, 0, None)


# --- Decode --------------------------------------------------------------------
def test_resource_name_maps_known_icons_and_passes_through_unknowns():
    assert resource_name("item_icon_104") == "coal"
    assert resource_name("item_icon_105") == "iron"
    assert resource_name("item_icon_100081") == "fire_crystal"
    assert resource_name("item_icon_100082") == "refined_fire_crystal"
    assert resource_name("item_icon_999") == "item_icon_999"   # unmapped → unchanged


def test_decode_cost_sums_same_resource_icons():
    # 102 and 100011 are both meat → merged; order is first-seen.
    cost = _decode_cost([
        {"item": "item_icon_102", "amount": "10"},
        {"item": "item_icon_104", "amount": "4"},
        {"item": "item_icon_100011", "amount": "6"},
    ])
    assert cost == (("meat", 16), ("coal", 4))


# --- Real graph end-to-end -----------------------------------------------------
def test_real_graph_costs_are_resource_named():
    graph = load_graph()
    furnace = graph.spec("furnace")
    # Furnace Lv 2 costs item_icon_103 (180) → decoded to wood.
    assert furnace.level("2").cost == (("wood", 180),)
    fc = graph.spec("fire_crystal_furnace")
    if fc is not None:                          # FC ladder carries the 4 basics + crystal
        keys = {res for res, _ in fc.level("30-1").cost}
        assert {"meat", "wood", "coal", "iron", "fire_crystal"} <= keys


# --- Bottleneck repair (now live) ----------------------------------------------
def _bottleneck_graph():
    furnace = BuildingSpec("furnace", "Furnace", (
        _lvl("1", 1.0),
        _lvl("2", 2.0, cost=(("coal", 1000),)),     # next furnace step needs coal
    ))
    coal_mine = BuildingSpec("coal_mine", "Coal Mine", (
        _lvl("1", 1.0, prereqs=(("furnace", 1.0),)),
        _lvl("2", 2.0, prereqs=(("furnace", 1.0),), cost=(("wood", 5),)),
    ))
    return BuildGraph(buildings={b.id: b for b in (furnace, coal_mine)})


def test_furnace_short_on_coal_triggers_the_coal_mine():
    graph = _bottleneck_graph()
    # Rich in wood, no coal → furnace L2 unaffordable, coal mine affordable.
    slate = plan_builds(graph, {"furnace": 1, "coal_mine": 1}, resources={"wood": 100})
    assert slate.picks[0].spec_id == "coal_mine"
    assert slate.picks[0].track == "bottleneck"      # value lifted above plain economy
    bottleneck = next(c for c in slate.candidates if c.track == "bottleneck")
    assert bottleneck.affordable
