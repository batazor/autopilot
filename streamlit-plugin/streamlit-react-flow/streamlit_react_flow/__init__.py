"""Streamlit component for diagrams and workflow editing via @xyflow/react (React Flow v12)."""

from __future__ import annotations

import os
from collections.abc import Sequence
from typing import Any, TypedDict

import streamlit.components.v1 as components

_RELEASE = True

if not _RELEASE:
    _component = components.declare_component("react_flow", url="http://localhost:3001")
else:
    _parent = os.path.dirname(os.path.abspath(__file__))
    _build = os.path.join(_parent, "frontend/build")
    _component = components.declare_component("react_flow", path=_build)


class FlowPosition(TypedDict):
    x: float
    y: float


class FlowNodeData(TypedDict, total=False):
    label: str
    background: str
    subtitle: str
    highlighted: bool
    dimmed: bool
    status: str  # initial | loading | success | error


class FlowNode(TypedDict, total=False):
    id: str
    type: str
    position: FlowPosition
    data: FlowNodeData
    style: dict[str, Any]


class FlowEdge(TypedDict, total=False):
    id: str
    source: str
    target: str
    type: str
    animated: bool
    label: str
    style: dict[str, Any]


class FlowLegendItem(TypedDict):
    label: str
    color: str
    kind: str  # node | edge | dashed-edge


class FlowEditorState(TypedDict, total=False):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    selectedNodeId: str | None


def react_flow(
    *,
    nodes: Sequence[FlowNode],
    edges: Sequence[FlowEdge],
    height: int = 500,
    width: int = 1100,
    key: str | None = None,
    highlight_nodes: Sequence[str] | None = None,
    highlight_edges: Sequence[tuple[str, str]] | None = None,
    selectable: bool = False,
    show_minimap: bool = False,
    show_controls: bool = True,
    fit_view: bool = True,
    legend_items: Sequence[FlowLegendItem] | None = None,
) -> str | None:
    """Render a read-only node graph (pan/zoom; optional node click)."""
    hi_nodes = list(highlight_nodes or ())
    hi_edges = [[s, t] for s, t in (highlight_edges or ())]
    value = _component(
        mode="view",
        nodes=list(nodes),
        edges=list(edges),
        height=int(height),
        width=int(width),
        highlightNodes=hi_nodes,
        highlightEdges=hi_edges,
        selectable=bool(selectable),
        showMinimap=bool(show_minimap),
        showControls=bool(show_controls),
        fitView=bool(fit_view),
        legendItems=list(legend_items or ()),
        key=key,
        default=None,
    )
    if value is None or value == "":
        return None
    return str(value)


def flow_editor(
    *,
    nodes: Sequence[FlowNode],
    edges: Sequence[FlowEdge],
    height: int = 700,
    width: int = 1100,
    key: str | None = None,
    edges_locked: bool = False,
    show_minimap: bool = True,
) -> FlowEditorState | None:
    """Interactive workflow-style graph editor (drag, connect, delete, search).

    Returns updated nodes/edges and selectedNodeId when the user edits the graph.
    """
    default: FlowEditorState = {
        "nodes": list(nodes),
        "edges": list(edges),
        "selectedNodeId": None,
    }
    raw = _component(
        mode="editor",
        nodes=list(nodes),
        edges=list(edges),
        height=int(height),
        width=int(width),
        edgesLocked=bool(edges_locked),
        showMinimap=bool(show_minimap),
        key=key,
        default=default,
    )
    if not raw or not isinstance(raw, dict):
        return None
    return raw  # type: ignore[return-value]


__all__ = [
    "FlowEdge",
    "FlowEditorState",
    "FlowLegendItem",
    "FlowNode",
    "FlowNodeData",
    "FlowPosition",
    "flow_editor",
    "react_flow",
]
