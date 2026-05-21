"""Screen routing graph for the Next.js Routes page."""
from __future__ import annotations

import itertools
from collections import deque
from typing import Any, Literal

from navigation.screen_graph import EDGE_DYNAMIC, EDGE_TAPS, bfs_route
from ui.flow_layout import (
    _NODE_WIDTH_PX,
    adjacency_from_edge_keys,
    build_flow_graph,
    sorted_edge_pairs,
    spanning_forest_edges,
)

GraphView = Literal["hub", "focus", "path", "full"]

_STATIC_TAP_EDGE_KEYS = frozenset(EDGE_TAPS.keys())
_DYNAMIC_EDGE_KEYS = frozenset(EDGE_DYNAMIC.keys())
_ROUTABLE_EDGE_KEYS = frozenset((*_STATIC_TAP_EDGE_KEYS, *_DYNAMIC_EDGE_KEYS))
_TAP_GRAPH = adjacency_from_edge_keys(_ROUTABLE_EDGE_KEYS)
_SCREEN_GRAPH = adjacency_from_edge_keys(_STATIC_TAP_EDGE_KEYS)
_GRAPH_ROOT = "main_city"


def tap_graph_nodes() -> list[str]:
    nodes: set[str] = set()
    for a, b in _STATIC_TAP_EDGE_KEYS:
        nodes.add(str(a))
        nodes.add(str(b))
    return sorted(nodes)


def plan_bot_route(src: str, dst: str) -> tuple[list[str] | None, str]:
    path = bfs_route(src, dst)
    if path is not None:
        return path, "direct"
    hub = "main_city"
    path_to_hub = bfs_route(src, hub)
    path_from_hub = bfs_route(hub, dst)
    if path_to_hub and path_from_hub:
        return path_to_hub + path_from_hub[1:], "via_main_city"
    return None, "direct"


def route_edge_pairs(path: list[str] | None) -> list[tuple[str, str]]:
    if not path:
        return []
    return [(a, b) for a, b in itertools.pairwise(path)]


def edge_status(src: str, dst: str) -> str:
    key = (src, dst)
    if key in EDGE_TAPS:
        return "static tap"
    if key in EDGE_DYNAMIC:
        return "dynamic tap"
    return "unknown"


def edge_action_summary(src: str, dst: str) -> str:
    key = (src, dst)
    if key in EDGE_TAPS:
        return ", ".join(t if isinstance(t, str) else str(t) for t in EDGE_TAPS[key])
    if key in EDGE_DYNAMIC:
        spec = EDGE_DYNAMIC[key]
        resolver = str(spec.get("resolver", "?"))
        target = spec.get("target")
        return f"resolver={resolver}" + (f", target={target}" if target is not None else "")
    return ""


def node_details(node_id: str) -> dict[str, Any]:
    outgoing = sorted(_SCREEN_GRAPH.get(node_id, frozenset()))
    incoming = sorted(src for src, dsts in _SCREEN_GRAPH.items() if node_id in dsts)
    rows = [
        {
            "dir": "in",
            "edge": f"{src} -> {node_id}",
            "status": edge_status(src, node_id),
        }
        for src in incoming
    ] + [
        {
            "dir": "out",
            "edge": f"{node_id} -> {dst}",
            "status": edge_status(node_id, dst),
        }
        for dst in outgoing
    ]
    return {
        "node_id": node_id,
        "incoming": len(incoming),
        "outgoing": len(outgoing),
        "edges": rows,
    }


def _children_map(tree_edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = {}
    for parent, child in tree_edges:
        children.setdefault(parent, []).append(child)
    for key in children:
        children[key].sort()
    return children


def _ancestors_in_tree(
    node_id: str,
    tree_edges: list[tuple[str, str]],
) -> set[str]:
    parents = {child: parent for parent, child in tree_edges}
    out = {node_id}
    cur = node_id
    while cur in parents:
        cur = parents[cur]
        out.add(cur)
    return out


def _descendants_in_tree(
    node_id: str,
    children: dict[str, list[str]],
    *,
    max_depth: int | None = None,
) -> set[str]:
    out = {node_id}
    queue: deque[tuple[str, int]] = deque([(node_id, 0)])
    while queue:
        nid, depth = queue.popleft()
        if max_depth is not None and depth >= max_depth:
            continue
        for kid in children.get(nid, ()):
            if kid not in out:
                out.add(kid)
                queue.append((kid, depth + 1))
    return out


def visible_nodes_for_view(
    nodes_list: list[str],
    tree_edges: list[tuple[str, str]],
    *,
    view: GraphView,
    root: str,
    focus: str | None,
    path: list[str] | None,
    hub_depth: int = 2,
    focus_depth: int = 3,
) -> set[str]:
    """Limit the rendered tree so wide graphs stay readable."""

    all_set = set(nodes_list)
    children = _children_map(tree_edges)

    if view == "full":
        return all_set

    if view == "path" and path:
        visible = set(path)
        for i in range(len(path) - 1):
            visible.add(path[i])
            visible.add(path[i + 1])
        return visible & all_set

    if view == "focus" and focus and focus in all_set:
        visible = _ancestors_in_tree(focus, tree_edges)
        visible |= _descendants_in_tree(
            focus,
            children,
            max_depth=focus_depth,
        )
        return visible & all_set

    # Default hub: main_city + limited depth in the spanning tree.
    hub = root if root in all_set else nodes_list[0] if nodes_list else ""
    if not hub:
        return all_set
    return _descendants_in_tree(hub, children, max_depth=hub_depth) & all_set


def build_graph_payload(
    *,
    route_from: str | None = None,
    route_to: str | None = None,
    focus: str | None = None,
    view: GraphView = "hub",
    hub_depth: int = 2,
) -> dict[str, Any]:
    nodes_list = tap_graph_nodes()
    screen_pairs = sorted_edge_pairs(_SCREEN_GRAPH)
    total_screens = len(nodes_list)

    path: list[str] | None = None
    mode = "direct"
    if route_from and route_to:
        path, mode = plan_bot_route(route_from, route_to)

    highlight_nodes: set[str] = set(path or [])
    if focus:
        highlight_nodes.add(focus)
    highlight_edges = route_edge_pairs(path)

    tree_pairs = spanning_forest_edges(nodes_list, screen_pairs, _GRAPH_ROOT)
    visible = visible_nodes_for_view(
        nodes_list,
        tree_pairs,
        view=view,
        root=_GRAPH_ROOT,
        focus=focus,
        path=path,
        hub_depth=max(1, hub_depth),
    )
    visible_list = sorted(visible)
    tree_pairs_vis = [
        (a, b) for a, b in tree_pairs if a in visible and b in visible
    ]
    tree_graph = adjacency_from_edge_keys(tree_pairs_vis)

    flow_nodes, flow_edges, height, width = build_flow_graph(
        tree_graph,
        highlight_nodes=highlight_nodes & visible,
        highlight_edges=highlight_edges,
        tap_edge_keys=_STATIC_TAP_EDGE_KEYS,
        dynamic_edge_keys=None,
        layout_root=_GRAPH_ROOT if _GRAPH_ROOT in visible_list else None,
        tree_edges=tree_pairs_vis,
        show_edge_labels=False,
        layout_slot_w=float(_NODE_WIDTH_PX + 56),
        layout_slot_h=220.0,
        layout_margin=120.0,
    )

    hops: list[dict[str, str]] = []
    if path and len(path) > 1:
        for i, (a, b) in enumerate(itertools.pairwise(path)):
            hops.append(
                {
                    "n": str(i + 1),
                    "hop": f"{a} -> {b}",
                    "status": edge_status(a, b),
                    "action": edge_action_summary(a, b),
                }
            )

    return {
        "metrics": {
            "page_transitions": len(screen_pairs),
            "tree_edges": len(tree_pairs),
            "static_edges": len(_STATIC_TAP_EDGE_KEYS),
            "dynamic_edges": len(_DYNAMIC_EDGE_KEYS),
            "screens": total_screens,
        },
        "nodes": flow_nodes,
        "edges": flow_edges,
        "height": height,
        "width": width,
        "screens": nodes_list,
        "visible_screens": visible_list,
        "visible_count": len(visible_list),
        "total_screens": total_screens,
        "view": view,
        "path": path,
        "mode": mode,
        "hops": hops,
    }


def list_edges(
    *,
    query: str = "",
    statuses: list[str] | None = None,
) -> dict[str, Any]:
    wanted = set(statuses or ["static tap"])
    q = query.strip().lower()
    rows: list[dict[str, str]] = []
    for src, dst in sorted_edge_pairs(_SCREEN_GRAPH):
        status = edge_status(src, dst)
        action = edge_action_summary(src, dst)
        haystack = " ".join((src, dst, status, action)).lower()
        if status not in wanted or (q and q not in haystack):
            continue
        rows.append(
            {
                "from": src,
                "to": dst,
                "status": status,
                "action": action,
            }
        )
    total = len(sorted_edge_pairs(_SCREEN_GRAPH))
    return {"edges": rows, "total": total, "shown": len(rows)}
