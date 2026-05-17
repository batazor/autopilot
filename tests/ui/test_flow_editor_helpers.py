"""Tests for flow editor layout helpers."""

from __future__ import annotations

from typing import cast

from streamlit_react_flow import FlowNode

from ui.flow_layout import edges_to_adjacency, merge_editor_positions


def test_edges_to_adjacency() -> None:
    adj = edges_to_adjacency(
        [
            {"id": "e0", "source": "a", "target": "b"},
            {"id": "e1", "source": "a", "target": "c"},
        ]
    )
    assert adj["a"] == frozenset({"b", "c"})


def test_merge_editor_positions() -> None:
    nodes: list[FlowNode] = [
        cast("FlowNode", {"id": "a", "type": "workflow", "position": {"x": 0, "y": 0}, "data": {"label": "a"}}),
    ]
    saved: list[FlowNode] = [
        cast("FlowNode", {"id": "a", "type": "workflow", "position": {"x": 99, "y": 50}, "data": {"label": "a"}}),
    ]
    merged = merge_editor_positions(nodes, saved)
    assert merged[0]["position"] == {"x": 99, "y": 50}
