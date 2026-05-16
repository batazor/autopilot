"""Tests for screen-route graph layout helpers."""

from __future__ import annotations

from ui.flow_layout import (
    build_flow_graph,
    layout_hierarchical,
    sorted_edge_pairs,
)


def test_sorted_edge_pairs_stable() -> None:
    edges = {"b": frozenset({"a"}), "a": frozenset({"c"})}
    assert sorted_edge_pairs(edges) == [("a", "c"), ("b", "a")]


def test_layout_dag_single_layer() -> None:
    pos = layout_hierarchical(["a", "b"], [("a", "b")], canvas_w=400, canvas_h=300)
    assert pos["a"]["y"] <= pos["b"]["y"]


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
