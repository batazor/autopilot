"""Build React Flow nodes/edges and canvas layout for screen-route graphs."""
from __future__ import annotations

import math
from collections.abc import Iterable

import networkx as nx
from streamlit_react_flow import FlowEdge, FlowNode

REGION_BG: dict[str, str] = {
    "Tundra Adventure": "#dbeafe",
    "Main Menu": "#f3e8ff",
    "Troops": "#dcfce7",
    "multi": "#fef9c3",
    "none": "#f4f4f5",
}

_NODE_WIDTH_PX = 240
_SLOT_W = 304.0
_SLOT_H = 168.0
_NORM_MARGIN = 104.0

_HIGHLIGHT_EDGE_STYLE: dict[str, object] = {
    "stroke": "#ea580c",
    "strokeWidth": 2.5,
}
_NON_TAP_EDGE_STYLE: dict[str, object] = {
    "stroke": "#a1a1aa",
    "strokeDasharray": "6 4",
}
_DYNAMIC_EDGE_STYLE: dict[str, object] = {
    "stroke": "#2563eb",
    "strokeDasharray": "8 4",
    "strokeWidth": 2,
}


def sorted_edge_pairs(edges: dict[str, frozenset[str]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for src in sorted(edges.keys()):
        for dst in sorted(edges[src]):
            rows.append((src, dst))
    return rows


def screen_to_regions(
    regions: tuple[tuple[str, dict[str, frozenset[str]]], ...],
) -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for label, g in regions:
        for src, dsts in g.items():
            out.setdefault(src, set()).add(label)
            for d in dsts:
                out.setdefault(d, set()).add(label)
    return {k: frozenset(v) for k, v in out.items()}


def _node_group(
    nid: str,
    *,
    default_region: str | None,
    screen_regions: dict[str, frozenset[str]] | None,
) -> str:
    if default_region:
        return default_region
    if screen_regions is None:
        return "none"
    r = screen_regions.get(nid, frozenset())
    if len(r) > 1:
        return "multi"
    if len(r) == 1:
        return next(iter(r))
    return "none"


def _node_subtitle(
    nid: str,
    *,
    default_region: str | None,
    screen_regions: dict[str, frozenset[str]] | None,
) -> str:
    if default_region:
        return default_region
    if screen_regions is None:
        return ""
    regions = screen_regions.get(nid, frozenset())
    if not regions:
        return ""
    return " · ".join(sorted(regions))


def _normalize_xy(
    raw: dict[str, tuple[float, float]],
    *,
    target_w: float,
    target_h: float,
    margin: float,
) -> dict[str, dict[str, float]]:
    if not raw:
        return {}
    xs = [raw[n][0] for n in raw]
    ys = [raw[n][1] for n in raw]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    span_x = max(max_x - min_x, 1e-6)
    span_y = max(max_y - min_y, 1e-6)
    inner_w = target_w - 2 * margin
    inner_h = target_h - 2 * margin
    out: dict[str, dict[str, float]] = {}
    for nid in raw:
        px = margin + (raw[nid][0] - min_x) / span_x * inner_w
        py = margin + (raw[nid][1] - min_y) / span_y * inner_h
        out[nid] = {"x": float(px), "y": float(py)}
    return out


def layout_hierarchical(
    node_ids: list[str],
    edge_pairs: list[tuple[str, str]],
    *,
    canvas_w: float,
    canvas_h: float,
    slot_w: float = _SLOT_W,
    slot_h: float = _SLOT_H,
    norm_margin: float = _NORM_MARGIN,
) -> dict[str, dict[str, float]]:
    """Layered top-to-bottom for a DAG; spring layout if the graph has cycles."""

    nset = set(node_ids)
    pairs = [(u, v) for u, v in edge_pairs if u in nset and v in nset]
    if not node_ids:
        return {}
    if len(node_ids) == 1:
        return {node_ids[0]: {"x": canvas_w / 2, "y": canvas_h / 2}}

    g = nx.DiGraph()
    g.add_nodes_from(node_ids)
    g.add_edges_from(pairs)

    if nx.is_directed_acyclic_graph(g):
        generations = list(nx.topological_generations(g))
        raw: dict[str, tuple[float, float]] = {}
        for row, layer in enumerate(generations):
            ordered = sorted(layer, key=str)
            n_l = len(ordered)
            row_width = max((n_l - 1) * slot_w, 0)
            x0 = -row_width / 2
            for i, nid in enumerate(ordered):
                raw[nid] = (x0 + i * slot_w, row * slot_h)
        return _normalize_xy(raw, target_w=canvas_w, target_h=canvas_h, margin=norm_margin)

    n_g = len(node_ids)
    spread_k = max(4.5, 14.0 / math.sqrt(n_g))
    pos = nx.spring_layout(
        g.to_undirected(),
        k=spread_k,
        iterations=160,
        seed=42,
        dim=2,
    )
    raw2 = {nid: (float(pos[nid][0]), float(pos[nid][1])) for nid in node_ids if nid in pos}
    return _normalize_xy(raw2, target_w=canvas_w, target_h=canvas_h, margin=norm_margin)


def _canvas_size(node_ids: list[str], pairs: list[tuple[str, str]]) -> tuple[float, float]:
    g = nx.DiGraph()
    g.add_nodes_from(node_ids)
    g.add_edges_from(pairs)
    if len(node_ids) <= 1:
        return 1100.0, 420.0
    if nx.is_directed_acyclic_graph(g):
        gens = list(nx.topological_generations(g))
        max_row = max((len(layer) for layer in gens), default=1)
        n_layers = len(gens)
        canvas_w = max(1120.0, _NORM_MARGIN * 2 + max_row * _SLOT_W)
        canvas_h = max(440.0, _NORM_MARGIN * 2 + n_layers * _SLOT_H)
    else:
        n_g = len(node_ids)
        canvas_w = max(1120.0, _NORM_MARGIN * 2 + math.sqrt(n_g) * _SLOT_W * 1.35)
        canvas_h = max(440.0, _NORM_MARGIN * 2 + math.sqrt(n_g) * _SLOT_H * 1.35)
    return float(min(4200, canvas_w)), float(min(2400, canvas_h))


def build_flow_graph(
    edges: dict[str, frozenset[str]],
    *,
    default_region: str | None = None,
    screen_regions: dict[str, frozenset[str]] | None = None,
    highlight_nodes: Iterable[str] | None = None,
    highlight_edges: Iterable[tuple[str, str]] | None = None,
    tap_edge_keys: frozenset[tuple[str, str]] | None = None,
    dynamic_edge_keys: frozenset[tuple[str, str]] | None = None,
    node_type: str = "screen",
) -> tuple[list[FlowNode], list[FlowEdge], int, int]:
    """Build React Flow payload and canvas dimensions."""
    highlight_node_set = set(highlight_nodes or ())
    highlight_edge_set = set(highlight_edges or ())

    node_ids: set[str] = set()
    for s, ds in edges.items():
        node_ids.add(s)
        node_ids.update(ds)
    ordered = sorted(node_ids)
    pairs = sorted_edge_pairs(edges)

    canvas_w, canvas_h = _canvas_size(ordered, pairs)
    positions = layout_hierarchical(
        ordered,
        pairs,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
    )

    nodes: list[FlowNode] = []
    for nid in ordered:
        grp = _node_group(nid, default_region=default_region, screen_regions=screen_regions)
        bg = REGION_BG.get(grp, "#f4f4f5")
        data: dict[str, object] = {
            "label": nid,
            "background": bg,
            "highlighted": nid in highlight_node_set,
        }
        subtitle = _node_subtitle(nid, default_region=default_region, screen_regions=screen_regions)
        if subtitle:
            data["subtitle"] = subtitle
        if node_type == "workflow":
            data["subtitle"] = grp
        nodes.append(
            {
                "id": nid,
                "type": node_type,
                "data": data,
                "position": positions[nid],
            }
        )

    flow_edges: list[FlowEdge] = []
    for i, (src, dst) in enumerate(pairs):
        pair = (src, dst)
        edge: FlowEdge = {
            "id": f"e{i}",
            "source": src,
            "target": dst,
            "type": "smoothstep",
        }
        if pair in highlight_edge_set:
            edge["animated"] = True
            edge["style"] = dict(_HIGHLIGHT_EDGE_STYLE)
        elif dynamic_edge_keys is not None and pair in dynamic_edge_keys:
            edge["style"] = dict(_DYNAMIC_EDGE_STYLE)
            edge["label"] = "dynamic"
        elif tap_edge_keys is not None and pair not in tap_edge_keys:
            edge["style"] = dict(_NON_TAP_EDGE_STYLE)
            edge["label"] = "topology"
        flow_edges.append(edge)

    return nodes, flow_edges, int(canvas_h), int(canvas_w)


def adjacency_from_edge_keys(edge_keys: Iterable[tuple[str, str]]) -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for a, b in edge_keys:
        out.setdefault(str(a), set()).add(str(b))
    return {k: frozenset(v) for k, v in out.items()}


def edges_to_adjacency(flow_edges: Iterable[FlowEdge]) -> dict[str, frozenset[str]]:
    out: dict[str, set[str]] = {}
    for e in flow_edges:
        out.setdefault(str(e["source"]), set()).add(str(e["target"]))
    return {k: frozenset(v) for k, v in out.items()}


_STEP_FLOW_SLOT_W = 268.0
_STEP_FLOW_H = 200.0


def build_scenario_step_flow(
    summaries: tuple[str, ...],
    *,
    current_step: int = 0,
    is_running: bool = False,
    idle_start_step: int = 0,
) -> tuple[list[FlowNode], list[FlowEdge], int, int]:
    """Horizontal pipeline of DSL steps with per-node execution status."""
    n = len(summaries)
    if n == 0:
        return [], [], 120, 480

    nodes: list[FlowNode] = []
    for i, summary in enumerate(summaries):
        if is_running:
            if i < current_step:
                status = "success"
            elif i == current_step:
                status = "loading"
            else:
                status = "initial"
        else:
            pick = max(0, min(idle_start_step, n - 1))
            if i < pick:
                status = "success"
            elif i == pick:
                status = "loading"
            else:
                status = "initial"

        text = summary if len(summary) <= 72 else f"{summary[:69]}…"
        nodes.append(
            {
                "id": f"step-{i}",
                "type": "workflow",
                "position": {"x": float(i * _STEP_FLOW_SLOT_W), "y": 36.0},
                "data": {
                    "label": f"Step {i}",
                    "subtitle": text,
                    "status": status,
                },
            }
        )

    flow_edges: list[FlowEdge] = []
    for i in range(n - 1):
        edge: FlowEdge = {
            "id": f"se{i}",
            "source": f"step-{i}",
            "target": f"step-{i + 1}",
            "type": "smoothstep",
        }
        if is_running and i == current_step - 1 and current_step > 0:
            edge["animated"] = True
            edge["style"] = {"stroke": "#6366f1", "strokeWidth": 2}
        flow_edges.append(edge)

    width = int(min(4200, max(520, n * _STEP_FLOW_SLOT_W + 120)))
    return nodes, flow_edges, int(_STEP_FLOW_H), width


def merge_editor_positions(
    nodes: list[FlowNode],
    saved: list[FlowNode] | None,
) -> list[FlowNode]:
    """Keep user-dragged positions from a prior editor session."""
    if not saved:
        return nodes
    pos_by_id = {n["id"]: n["position"] for n in saved if "position" in n}
    merged: list[FlowNode] = []
    for node in nodes:
        copy = dict(node)
        pid = copy["id"]
        if pid in pos_by_id:
            copy["position"] = pos_by_id[pid]
        merged.append(copy)  # type: ignore[arg-type]
    return merged
