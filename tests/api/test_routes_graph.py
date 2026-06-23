from __future__ import annotations

from api.services import routes_graph as rg


def test_visible_nodes_hub_depth_limits_count() -> None:
    nodes = rg.tap_graph_nodes()
    from dashboard.flow_layout import adjacency_from_edge_keys, sorted_edge_pairs, spanning_forest_edges
    from navigation.screen_graph import EDGE_TAPS

    g = adjacency_from_edge_keys(frozenset(EDGE_TAPS.keys()))
    pairs = sorted_edge_pairs(g)
    tree = spanning_forest_edges(nodes, pairs, "main_city")

    full = rg.visible_nodes_for_view(nodes, tree, view="full", root="main_city", focus=None, path=None)
    hub1 = rg.visible_nodes_for_view(
        nodes, tree, view="hub", root="main_city", focus=None, path=None, hub_depth=1
    )
    assert len(full) == len(nodes)
    assert "main_city" in hub1
    assert len(hub1) < len(full)


def test_screen_zones_invariants() -> None:
    out = rg.screen_zones("main_city")

    assert out["screen_id"] == "main_city"
    assert out["has_reference"] is True
    zones = out["zones"]
    assert zones, "main_city should expose drawable zones"

    transitions = [z for z in zones if z["kind"] == "transition"]
    regions = [z for z in zones if z["kind"] == "region"]
    # main_city is the hub: it must have outgoing transition zones to draw.
    assert transitions

    # Counts mirror the drawn zones exactly.
    assert out["counts"]["transitions"] == len(transitions)
    assert out["counts"]["regions"] == len(regions)
    assert out["counts"]["unmapped"] == len(out["unmapped"])

    # Every zone carries a numeric percent bbox.
    for z in zones:
        bbox = z["bbox"]
        assert {"x", "y", "width", "height"} <= bbox.keys()
        assert all(isinstance(bbox[k], float) for k in ("x", "y", "width", "height"))

    # Transition zones name a destination + status; a region never doubles as one.
    for z in transitions:
        assert z["to"] and z["status"] == "static tap"
    transition_regions = {z["region"] for z in transitions}
    assert transition_regions.isdisjoint({z["region"] for z in regions})

    # Unmapped rows (dynamic / unresolved taps) are structurally complete.
    for u in out["unmapped"]:
        assert u["to"] and u["region"] and u["status"]


def test_screen_zones_unknown_screen_is_lenient() -> None:
    out = rg.screen_zones("definitely_not_a_screen")
    assert out["has_reference"] is False
    assert out["zones"] == []
    assert out["counts"] == {"transitions": 0, "regions": 0, "unmapped": 0}
