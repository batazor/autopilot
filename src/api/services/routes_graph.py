"""Screen routing graph for the Next.js Routes page."""
from __future__ import annotations

import itertools
from collections import deque
from typing import Any, Literal

from dashboard.flow_layout import (
    _NODE_WIDTH_PX,
    adjacency_from_edge_keys,
    build_flow_graph,
    sorted_edge_pairs,
    spanning_forest_edges,
)
from navigation.screen_graph import (
    EDGE_DYNAMIC,
    EDGE_TAPS,
    bfs_route,
    graph_for_game,
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


def _bbox_pct(reg: dict[str, Any]) -> dict[str, float] | None:
    """Pull the percent-of-reference bbox (x/y/width/height) off a region dict."""
    bbox = reg.get("bbox")
    if not isinstance(bbox, dict):
        return None
    try:
        return {
            "x": float(bbox.get("x", 0.0)),
            "y": float(bbox.get("y", 0.0)),
            "width": float(bbox.get("width", 0.0)),
            "height": float(bbox.get("height", 0.0)),
        }
    except (TypeError, ValueError):
        return None


def _tap_region_name(tap: Any) -> str | None:
    """Region name for a static tap entry (``str`` shorthand or ``{region: ...}``)."""
    if isinstance(tap, str):
        return tap.strip() or None
    if isinstance(tap, dict):
        rn = tap.get("region")
        if isinstance(rn, str):
            return rn.strip() or None
    return None


def screen_zones(screen_id: str) -> dict[str, Any]:
    """Transition tap-zones + labeled regions for one screen (overlay view).

    For ``screen_id`` we resolve every outgoing edge's tap region to a bbox
    (percent of the 720×1280 reference) so the UI can draw the zone directly
    over the real reference screenshot. Labeled regions that are *not* a
    transition tap are returned too, so the operator can spot interactive areas
    that have no edge yet — i.e. transitions still missing from the markup.
    Dynamic edges (runtime-resolved taps) have no static bbox and are reported
    under ``unmapped`` instead of drawn.
    """
    from config.paths import repo_root
    from layout.area_lookup import screen_region_by_name
    from layout.area_manifest import load_area_doc

    static, dynamic, _graph = graph_for_game()
    area_doc = load_area_doc(repo_root())

    # A screen_id can be contributed by several modules — area_doc.screens is a
    # flat list with no merge-by-id, so gather every entry that owns this screen.
    screen_entries = [
        entry
        for entry in area_doc.get("screens") or []
        if isinstance(entry, dict) and str(entry.get("screen_id") or "") == screen_id
    ]

    zones: list[dict[str, Any]] = []
    transition_regions: set[str] = set()
    unmapped: list[dict[str, str]] = []

    # Outgoing static-tap edges → resolve each tap region to a drawable bbox.
    for (src, dst), taps in static.items():
        if src != screen_id:
            continue
        for tap in taps:
            region_name = _tap_region_name(tap)
            box = None
            if region_name:
                pair = screen_region_by_name(area_doc, region_name)
                if pair is not None:
                    box = _bbox_pct(pair[1])
            if region_name and box is not None:
                transition_regions.add(region_name)
                zones.append(
                    {
                        "region": region_name,
                        "bbox": box,
                        "kind": "transition",
                        "to": dst,
                        "status": "static tap",
                        "action": region_name,
                    }
                )
            else:
                unmapped.append(
                    {
                        "to": dst,
                        "region": region_name or "(structured tap)",
                        "status": "static tap",
                    }
                )

    # Outgoing dynamic edges resolve their tap at runtime → no static bbox.
    for (src, dst), spec in dynamic.items():
        if src != screen_id:
            continue
        resolver = str(spec.get("resolver", "?")) if isinstance(spec, dict) else str(spec)
        unmapped.append({"to": dst, "region": f"resolver={resolver}", "status": "dynamic tap"})

    # Remaining labeled regions on this screen (no edge bound to them yet).
    seen_regions: set[str] = set()
    for entry in screen_entries:
        for reg in entry.get("regions") or []:
            if not isinstance(reg, dict):
                continue
            name = str(reg.get("name") or "")
            if not name or name in transition_regions or name in seen_regions:
                continue
            box = _bbox_pct(reg)
            if box is None:
                continue
            seen_regions.add(name)
            zones.append(
                {
                    "region": name,
                    "bbox": box,
                    "kind": "region",
                    "action": str(reg.get("action") or ""),
                    "has_red_dot": bool(reg.get("has_red_dot")),
                }
            )

    transitions = sum(1 for z in zones if z["kind"] == "transition")
    regions = sum(1 for z in zones if z["kind"] == "region")
    return {
        "screen_id": screen_id,
        "has_reference": bool(screen_entries),
        "zones": zones,
        "counts": {
            "transitions": transitions,
            "regions": regions,
            "unmapped": len(unmapped),
        },
        "unmapped": unmapped,
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
