"""Build React Flow nodes/edges and canvas layout for screen-route graphs."""
from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Any, cast

import networkx as nx

if TYPE_CHECKING:
    from collections.abc import Iterable

# React Flow node/edge payloads are plain dicts on the wire; the Next.js
# dashboard consumes them directly, and Python only ever passes them through.
FlowNode = dict[str, Any]
FlowEdge = dict[str, Any]

REGION_BG: dict[str, str] = {
    "Tundra Adventure": "#dbeafe",
    "Main Menu": "#f3e8ff",
    "Troops": "#dcfce7",
    "multi": "#fef9c3",
    "none": "#f4f4f5",
}

_NODE_WIDTH_PX = 240
_NODE_HEIGHT_PX = 56
_MIN_NODE_GAP_PX = 48.0
_SLOT_W = _NODE_WIDTH_PX + _MIN_NODE_GAP_PX
_SLOT_H = 200.0
_NORM_MARGIN = 120.0
_MAX_CANVAS_W = 80_000.0
_MAX_CANVAS_H = 20_000.0

_HIGHLIGHT_EDGE_STYLE: dict[str, Any] = {
    "stroke": "#ea580c",
    "strokeWidth": 2.5,
}
_NON_TAP_EDGE_STYLE: dict[str, Any] = {
    "stroke": "#a1a1aa",
    "strokeDasharray": "6 4",
}
_DYNAMIC_EDGE_STYLE: dict[str, Any] = {
    "stroke": "#2563eb",
    "strokeDasharray": "8 4",
    "strokeWidth": 2,
}
_TREE_EDGE_STYLE: dict[str, Any] = {
    "stroke": "#64748b",
    "strokeWidth": 1.5,
}


def sorted_edge_pairs(edges: dict[str, frozenset[str]]) -> list[tuple[str, str]]:
    return [
        (src, dst)
        for src in sorted(edges.keys())
        for dst in sorted(edges[src])
    ]


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
    """Map layout coordinates to canvas pixels without compressing node spacing."""

    if not raw:
        return {}
    xs = [raw[n][0] for n in raw]
    ys = [raw[n][1] for n in raw]
    min_x, _max_x = min(xs), max(xs)
    min_y, _max_y = min(ys), max(ys)
    out: dict[str, dict[str, float]] = {}
    for nid in raw:
        out[nid] = {
            "x": float(margin + (raw[nid][0] - min_x)),
            "y": float(margin + (raw[nid][1] - min_y)),
        }
    return out


def _canvas_from_positions(
    positions: dict[str, dict[str, float]],
    *,
    margin: float,
) -> tuple[float, float]:
    if not positions:
        return 1100.0, 420.0
    max_x = max(p["x"] for p in positions.values())
    max_y = max(p["y"] for p in positions.values())
    canvas_w = max(1120.0, margin + max_x + _NODE_WIDTH_PX + margin)
    canvas_h = max(440.0, margin + max_y + _NODE_HEIGHT_PX + margin)
    return (
        float(min(_MAX_CANVAS_W, canvas_w)),
        float(min(_MAX_CANVAS_H, canvas_h)),
    )


def _undirected_adjacency(
    edge_pairs: list[tuple[str, str]],
) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {}
    for u, v in edge_pairs:
        adj.setdefault(u, set()).add(v)
        adj.setdefault(v, set()).add(u)
    return adj


def _bfs_depths_from_root(
    node_ids: list[str],
    edge_pairs: list[tuple[str, str]],
    root_id: str,
) -> dict[str, int]:
    """Shortest-hop distance from *root_id* on the undirected edge set."""

    nset = set(node_ids)
    if root_id not in nset:
        return dict.fromkeys(node_ids, 0)
    adj = _undirected_adjacency(edge_pairs)
    depths: dict[str, int] = {root_id: 0}
    frontier = [root_id]
    hop = 0
    while frontier:
        hop += 1
        next_frontier: list[str] = []
        for nid in frontier:
            for nb in sorted(adj.get(nid, ())):
                if nb not in nset or nb in depths:
                    continue
                depths[nb] = hop
                next_frontier.append(nb)
        frontier = next_frontier
    orphan_depth = (max(depths.values()) + 1) if depths else 0
    for nid in node_ids:
        depths.setdefault(nid, orphan_depth)
    return depths


def spanning_forest_edges(
    node_ids: list[str],
    edge_pairs: list[tuple[str, str]],
    preferred_root: str | None = None,
) -> list[tuple[str, str]]:
    """BFS spanning forest as directed parent→child edges (one parent per node)."""

    nset = set(node_ids)
    adj = _undirected_adjacency([(u, v) for u, v in edge_pairs if u in nset and v in nset])
    remaining = set(nset)
    tree: list[tuple[str, str]] = []

    def grow_component(root: str) -> None:
        if root not in remaining:
            return
        queue: deque[str] = deque([root])
        remaining.discard(root)
        while queue:
            parent = queue.popleft()
            for child in sorted(adj.get(parent, ())):
                if child not in remaining:
                    continue
                remaining.discard(child)
                tree.append((parent, child))
                queue.append(child)

    if preferred_root and preferred_root in remaining:
        grow_component(preferred_root)
    while remaining:
        grow_component(min(remaining))
    return tree


def _children_from_tree(tree_edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for parent, child in tree_edges:
        children.setdefault(parent, []).append(child)
    for key in children:
        children[key].sort()
    return children


def _tree_roots(node_ids: list[str], tree_edges: list[tuple[str, str]]) -> list[str]:
    children = {c for _, c in tree_edges}
    roots = [n for n in node_ids if n not in children]
    return sorted(roots, key=str)


def layout_tree(
    node_ids: list[str],
    tree_edges: list[tuple[str, str]],
    *,
    preferred_root: str | None = None,
    canvas_w: float,
    canvas_h: float,
    slot_w: float = _SLOT_W,
    slot_h: float = _SLOT_H,
    norm_margin: float = _NORM_MARGIN,
    component_gap: float = _SLOT_W * 2,
) -> dict[str, dict[str, float]]:
    """Place nodes in a parent-centered tree (hub on top, children below)."""

    if not node_ids:
        return {}
    if len(node_ids) == 1:
        return {node_ids[0]: {"x": canvas_w / 2, "y": canvas_h / 2}}

    children = _children_from_tree(tree_edges)
    roots = _tree_roots(node_ids, tree_edges)
    if preferred_root and preferred_root in roots:
        roots = [preferred_root] + [r for r in roots if r != preferred_root]

    raw: dict[str, tuple[float, float]] = {}
    component_offset = 0.0

    def assign_subtree(
        root: str,
        depth_level: int,
        *,
        x_pos: dict[str, float],
        depth: dict[str, int],
        x_slot: list[float],
    ) -> tuple[float, float]:
        kids = children.get(root, [])
        if not kids:
            x = x_slot[0] * slot_w
            x_slot[0] += 1.0
            x_pos[root] = x
            depth[root] = depth_level
            return x, x
        child_ranges = [
            assign_subtree(kid, depth_level + 1, x_pos=x_pos, depth=depth, x_slot=x_slot)
            for kid in kids
        ]
        lo = child_ranges[0][0]
        hi = child_ranges[-1][1]
        x = (lo + hi) / 2.0
        x_pos[root] = x
        depth[root] = depth_level
        return lo, hi

    for root in roots:
        x_pos: dict[str, float] = {}
        depth: dict[str, int] = {}
        x_slot = [0.0]
        lo, hi = assign_subtree(root, 0, x_pos=x_pos, depth=depth, x_slot=x_slot)
        shift = component_offset - lo
        for nid in _subtree_nodes(root, children):
            raw[nid] = (x_pos[nid] + shift, float(depth[nid]) * slot_h)
        component_offset += (hi - lo) + component_gap

    return _normalize_xy(raw, target_w=canvas_w, target_h=canvas_h, margin=norm_margin)


def _subtree_nodes(root: str, children: dict[str, list[str]]) -> set[str]:
    out = {root}
    stack = [root]
    while stack:
        nid = stack.pop()
        for kid in children.get(nid, ()):
            out.add(kid)
            stack.append(kid)
    return out


def layout_from_root(
    node_ids: list[str],
    edge_pairs: list[tuple[str, str]],
    *,
    root_id: str,
    canvas_w: float,
    canvas_h: float,
    slot_w: float = _SLOT_W,
    slot_h: float = _SLOT_H,
    norm_margin: float = _NORM_MARGIN,
) -> dict[str, dict[str, float]]:
    """Top-to-bottom layers by hop distance from *root_id* (hub at the top)."""

    if not node_ids:
        return {}
    if len(node_ids) == 1:
        return {node_ids[0]: {"x": canvas_w / 2, "y": canvas_h / 2}}

    depths = _bfs_depths_from_root(node_ids, edge_pairs, root_id)
    by_depth: dict[int, list[str]] = {}
    for nid in node_ids:
        by_depth.setdefault(depths[nid], []).append(nid)

    raw: dict[str, tuple[float, float]] = {}
    for depth in sorted(by_depth.keys()):
        layer = sorted(by_depth[depth])
        n_l = len(layer)
        row_width = max((n_l - 1) * slot_w, 0)
        x0 = -row_width / 2
        for i, nid in enumerate(layer):
            raw[nid] = (x0 + i * slot_w, float(depth) * slot_h)
    return _normalize_xy(raw, target_w=canvas_w, target_h=canvas_h, margin=norm_margin)


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
            ordered = cast("list[str]", sorted(layer, key=str))
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


def _count_leaves(root: str, children: dict[str, list[str]]) -> int:
    kids = children.get(root, [])
    if not kids:
        return 1
    return sum(_count_leaves(k, children) for k in kids)


def _max_tree_depth(root: str, children: dict[str, list[str]]) -> int:
    kids = children.get(root, [])
    if not kids:
        return 0
    return 1 + max(_max_tree_depth(k, children) for k in kids)


def _canvas_size_tree(
    node_ids: list[str],
    tree_edges: list[tuple[str, str]],
    preferred_root: str | None,
    *,
    slot_w: float = _SLOT_W,
    slot_h: float = _SLOT_H,
    norm_margin: float = _NORM_MARGIN,
) -> tuple[float, float]:
    if len(node_ids) <= 1:
        return 1100.0, 420.0
    children = _children_from_tree(tree_edges)
    roots = _tree_roots(node_ids, tree_edges)
    main = preferred_root if preferred_root and preferred_root in roots else roots[0]
    leaves = _count_leaves(main, children)
    depth = _max_tree_depth(main, children)
    extra_roots = max(0, len(roots) - 1)
    canvas_w = max(1120.0, norm_margin * 2 + leaves * slot_w + _NODE_WIDTH_PX)
    if extra_roots:
        canvas_w += extra_roots * slot_w * 4
    canvas_h = max(440.0, norm_margin * 2 + (depth + 1) * slot_h + _NODE_HEIGHT_PX)
    return float(min(_MAX_CANVAS_W, canvas_w)), float(min(_MAX_CANVAS_H, canvas_h))


def _canvas_size(
    node_ids: list[str],
    pairs: list[tuple[str, str]],
    *,
    layout_root: str | None = None,
    tree_edges: list[tuple[str, str]] | None = None,
    slot_w: float = _SLOT_W,
    slot_h: float = _SLOT_H,
    norm_margin: float = _NORM_MARGIN,
) -> tuple[float, float]:
    if len(node_ids) <= 1:
        return 1100.0, 420.0
    if tree_edges is not None:
        return _canvas_size_tree(
            node_ids,
            tree_edges,
            layout_root,
            slot_w=slot_w,
            slot_h=slot_h,
            norm_margin=norm_margin,
        )
    if layout_root and layout_root in node_ids:
        depths = _bfs_depths_from_root(node_ids, pairs, layout_root)
        by_depth: dict[int, int] = {}
        for d in depths.values():
            by_depth[d] = by_depth.get(d, 0) + 1
        max_row = max(by_depth.values(), default=1)
        n_layers = max(depths.values()) + 1
        canvas_w = max(1120.0, norm_margin * 2 + max_row * slot_w + _NODE_WIDTH_PX)
        canvas_h = max(440.0, norm_margin * 2 + n_layers * slot_h + _NODE_HEIGHT_PX)
        return float(min(_MAX_CANVAS_W, canvas_w)), float(min(_MAX_CANVAS_H, canvas_h))

    g = nx.DiGraph()
    g.add_nodes_from(node_ids)
    g.add_edges_from(pairs)
    if nx.is_directed_acyclic_graph(g):
        gens = list(nx.topological_generations(g))
        max_row = max((len(layer) for layer in gens), default=1)
        n_layers = len(gens)
        canvas_w = max(1120.0, norm_margin * 2 + max_row * slot_w)
        canvas_h = max(440.0, norm_margin * 2 + n_layers * slot_h)
    else:
        n_g = len(node_ids)
        canvas_w = max(1120.0, norm_margin * 2 + math.sqrt(n_g) * slot_w * 1.35)
        canvas_h = max(440.0, norm_margin * 2 + math.sqrt(n_g) * slot_h * 1.35)
    return float(min(_MAX_CANVAS_W, canvas_w)), float(min(_MAX_CANVAS_H, canvas_h))


def build_flow_graph(
    edges: dict[str, frozenset[str]],
    *,
    default_region: str | None = None,
    screen_regions: dict[str, frozenset[str]] | None = None,
    highlight_nodes: Iterable[str] | None = None,
    highlight_edges: Iterable[tuple[str, str]] | None = None,
    tap_edge_keys: frozenset[tuple[str, str]] | None = None,
    dynamic_edge_keys: frozenset[tuple[str, str]] | None = None,
    layout_root: str | None = None,
    tree_edges: list[tuple[str, str]] | None = None,
    node_type: str = "screen",
    show_edge_labels: bool = True,
    layout_slot_w: float = _SLOT_W,
    layout_slot_h: float = _SLOT_H,
    layout_margin: float = _NORM_MARGIN,
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
    display_tree = tree_edges is not None

    canvas_w, canvas_h = _canvas_size(
        ordered,
        pairs,
        layout_root=layout_root,
        tree_edges=tree_edges,
        slot_w=layout_slot_w,
        slot_h=layout_slot_h,
        norm_margin=layout_margin,
    )
    if display_tree and tree_edges:
        positions = layout_tree(
            ordered,
            tree_edges,
            preferred_root=layout_root,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            slot_w=layout_slot_w,
            slot_h=layout_slot_h,
            norm_margin=layout_margin,
        )
    elif layout_root and layout_root in ordered:
        positions = layout_from_root(
            ordered,
            pairs,
            root_id=layout_root,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            slot_w=layout_slot_w,
            slot_h=layout_slot_h,
            norm_margin=layout_margin,
        )
    else:
        positions = layout_hierarchical(
            ordered,
            pairs,
            canvas_w=canvas_w,
            canvas_h=canvas_h,
            slot_w=layout_slot_w,
            slot_h=layout_slot_h,
            norm_margin=layout_margin,
        )

    nodes: list[FlowNode] = []
    for nid in ordered:
        grp = _node_group(nid, default_region=default_region, screen_regions=screen_regions)
        bg = REGION_BG.get(grp, "#f4f4f5")
        data: dict[str, Any] = {
            "label": nid,
            "background": bg,
            "highlighted": nid in highlight_node_set,
            "is_hub": layout_root is not None and nid == layout_root,
        }
        subtitle = _node_subtitle(nid, default_region=default_region, screen_regions=screen_regions)
        if subtitle:
            data["subtitle"] = subtitle
        if node_type == "workflow":
            data["subtitle"] = grp
        nodes.append(
            cast(
                "FlowNode",
                {
                    "id": nid,
                    "type": node_type,
                    "data": data,
                    "position": positions[nid],
                },
            )
        )

    tree_pair_set = set(tree_edges or ())
    render_pairs = list(pairs)
    if display_tree:
        render_pairs.extend(
            pair
            for pair in sorted(highlight_edge_set)
            if pair not in tree_pair_set and pair[0] in node_ids and pair[1] in node_ids
        )

    flow_edges: list[FlowEdge] = []
    for i, (src, dst) in enumerate(render_pairs):
        pair = (src, dst)
        edge: dict[str, Any] = {
            "id": f"e{i}",
            "source": src,
            "target": dst,
            "type": "smoothstep",
        }
        if pair in highlight_edge_set:
            edge["animated"] = True
            edge["style"] = dict(_HIGHLIGHT_EDGE_STYLE)
        elif pair not in tree_pair_set and display_tree:
            edge["style"] = dict(_NON_TAP_EDGE_STYLE)
            if show_edge_labels:
                edge["label"] = "route"
        elif dynamic_edge_keys is not None and pair in dynamic_edge_keys:
            edge["style"] = dict(_DYNAMIC_EDGE_STYLE)
            if show_edge_labels:
                edge["label"] = "dynamic"
        elif tap_edge_keys is not None and pair not in tap_edge_keys:
            edge["style"] = dict(_NON_TAP_EDGE_STYLE)
            if show_edge_labels:
                edge["label"] = "topology"
        elif display_tree and pair in tree_pair_set:
            edge["style"] = dict(_TREE_EDGE_STYLE)
        flow_edges.append(cast("FlowEdge", edge))

    canvas_w, canvas_h = _canvas_from_positions(positions, margin=layout_margin)
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
        edge: dict[str, Any] = {
            "id": f"se{i}",
            "source": f"step-{i}",
            "target": f"step-{i + 1}",
            "type": "smoothstep",
        }
        if is_running and i == current_step - 1 and current_step > 0:
            edge["animated"] = True
            edge["style"] = {"stroke": "#6366f1", "strokeWidth": 2}
        flow_edges.append(cast("FlowEdge", edge))

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
        merged.append(cast("FlowNode", copy))
    return merged
