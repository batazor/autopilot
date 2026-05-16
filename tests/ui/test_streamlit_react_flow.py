"""Smoke tests for the local streamlit-react-flow component API."""

from __future__ import annotations

from streamlit_react_flow import FlowEdge, FlowNode, react_flow


def test_react_flow_api() -> None:
    nodes: list[FlowNode] = [
        {
            "id": "a",
            "type": "screen",
            "position": {"x": 0, "y": 0},
            "data": {"label": "A", "background": "#fff"},
        },
    ]
    edges: list[FlowEdge] = [{"id": "e0", "source": "a", "target": "b", "type": "smoothstep"}]
    assert callable(react_flow)
    assert nodes[0]["data"]["label"] == "A"
    assert edges[0]["source"] == "a"
