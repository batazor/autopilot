"""Tests for screen-route graph layout helpers."""

from __future__ import annotations

from dashboard.flow_layout import (
    build_flow_graph,
    layout_from_root,
    layout_hierarchical,
    layout_tree,
    sorted_edge_pairs,
    spanning_forest_edges,
)


def test_sorted_edge_pairs_stable() -> None:
    edges = {"b": frozenset({"a"}), "a": frozenset({"c"})}
    assert sorted_edge_pairs(edges) == [("a", "c"), ("b", "a")]


def test_layout_dag_single_layer() -> None:
    pos = layout_hierarchical(["a", "b"], [("a", "b")], canvas_w=400, canvas_h=300)
    assert pos["a"]["y"] <= pos["b"]["y"]


def test_layout_from_root_places_hub_above_neighbors() -> None:
    pos = layout_from_root(
        ["main_city", "shop", "heroes"],
        [("main_city", "shop"), ("main_city", "heroes")],
        root_id="main_city",
        canvas_w=800,
        canvas_h=600,
    )
    assert pos["main_city"]["y"] <= pos["shop"]["y"]
    assert pos["main_city"]["y"] <= pos["heroes"]["y"]


def test_spanning_forest_is_tree_from_hub() -> None:
    pairs = [
        ("main_city", "shop"),
        ("shop", "main_city"),
        ("main_city", "heroes"),
    ]
    tree = spanning_forest_edges(["main_city", "shop", "heroes"], pairs, "main_city")
    assert len(tree) == 2
    assert ("main_city", "shop") in tree or ("main_city", "heroes") in tree
    assert ("shop", "main_city") not in tree


def test_build_flow_graph_tree_has_one_edge_per_child() -> None:
    tree = [("main_city", "shop"), ("main_city", "heroes")]
    edges = {"main_city": frozenset({"shop", "heroes"})}
    _nodes, flow_edges, _h, _w = build_flow_graph(
        edges,
        tap_edge_keys=frozenset(tree),
        layout_root="main_city",
        tree_edges=tree,
    )
    assert len(flow_edges) == 2


def test_layout_tree_centers_parent_over_children() -> None:
    tree = [("main_city", "shop"), ("main_city", "heroes")]
    pos = layout_tree(
        ["main_city", "shop", "heroes"],
        tree,
        preferred_root="main_city",
        canvas_w=900,
        canvas_h=700,
    )
    hub_x = pos["main_city"]["x"]
    assert pos["shop"]["x"] < hub_x < pos["heroes"]["x"] or pos["heroes"]["x"] < hub_x < pos["shop"]["x"]
    assert pos["main_city"]["y"] < pos["shop"]["y"]


def test_layout_tree_keeps_horizontal_spacing_for_wide_hub() -> None:
    """Regression: canvas must not squash 100+ siblings into the same x."""

    children = [f"child_{i}" for i in range(40)]
    tree = [("main_city", c) for c in children]
    nodes = ["main_city", *children]
    pos = layout_tree(
        nodes,
        tree,
        preferred_root="main_city",
        canvas_w=20_000,
        canvas_h=800,
        slot_w=296.0,
        slot_h=200.0,
    )
    by_x = sorted(children, key=lambda n: pos[n]["x"])
    gaps = [
        pos[by_x[i + 1]]["x"] - pos[by_x[i]]["x"]
        for i in range(len(by_x) - 1)
    ]
    assert min(gaps) >= 200.0


def test_build_flow_graph_highlight_and_tap_styles() -> None:
    edges = {"a": frozenset({"b", "c"}), "b": frozenset()}
    tap_keys = frozenset({("a", "b")})
    nodes, flow_edges, _h, _w = build_flow_graph(
        edges,
        highlight_nodes=["a"],
        highlight_edges=[("a", "b")],
        tap_edge_keys=tap_keys,
    )
    assert all(n.get("type") == "screen" for n in nodes)
    node_a = next(n for n in nodes if n["id"] == "a")
    assert node_a["data"]["highlighted"] is True

    by_pair = {(e["source"], e["target"]): e for e in flow_edges}
    assert by_pair[("a", "b")].get("animated") is True
    assert "strokeDasharray" in (by_pair[("a", "c")].get("style") or {})
