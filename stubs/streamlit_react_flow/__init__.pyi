from collections.abc import Sequence
from typing import Any, TypedDict

__all__ = ['FlowEdge', 'FlowEditorState', 'FlowLegendItem', 'FlowNode', 'FlowNodeData', 'FlowPosition', 'flow_editor', 'react_flow']

class FlowPosition(TypedDict):
    x: float
    y: float

class FlowNodeData(TypedDict, total=False):
    label: str
    background: str
    subtitle: str
    highlighted: bool
    dimmed: bool
    status: str

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
    kind: str

class FlowEditorState(TypedDict, total=False):
    nodes: list[FlowNode]
    edges: list[FlowEdge]
    selectedNodeId: str | None

def react_flow(*, nodes: Sequence[FlowNode], edges: Sequence[FlowEdge], height: int = 500, width: int = 1100, key: str | None = None, highlight_nodes: Sequence[str] | None = None, highlight_edges: Sequence[tuple[str, str]] | None = None, selectable: bool = False, show_minimap: bool = False, show_controls: bool = True, fit_view: bool = True, legend_items: Sequence[FlowLegendItem] | None = None) -> str | None: ...
def flow_editor(*, nodes: Sequence[FlowNode], edges: Sequence[FlowEdge], height: int = 700, width: int = 1100, key: str | None = None, edges_locked: bool = False, show_minimap: bool = True) -> FlowEditorState | None: ...
