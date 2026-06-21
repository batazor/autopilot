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
