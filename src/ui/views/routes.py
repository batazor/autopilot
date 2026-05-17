"""Game node routing graph backed by the runtime tap registry."""
from __future__ import annotations

import itertools
from typing import Any

import streamlit as st
from streamlit_nested_table import nested_table, table_column
from streamlit_react_flow import FlowLegendItem, react_flow

from navigation.screen_graph import EDGE_DYNAMIC, EDGE_TAPS, bfs_route
from ui.flow_layout import adjacency_from_edge_keys, build_flow_graph, sorted_edge_pairs

_STATIC_TAP_EDGE_KEYS = frozenset(EDGE_TAPS.keys())
_DYNAMIC_EDGE_KEYS = frozenset(EDGE_DYNAMIC.keys())
_ROUTABLE_EDGE_KEYS = frozenset((*_STATIC_TAP_EDGE_KEYS, *_DYNAMIC_EDGE_KEYS))
_TAP_GRAPH = adjacency_from_edge_keys(_ROUTABLE_EDGE_KEYS)
_SCREEN_REGIONS: dict[str, frozenset[str]] = {}

_LEGEND: list[FlowLegendItem] = [
    {"label": "registered tap", "color": "#111827", "kind": "edge"},
    {"label": "dynamic tap", "color": "#2563eb", "kind": "dashed-edge"},
    {"label": "planned route", "color": "#ea580c", "kind": "edge"},
]


def _render_flow(
    edges: dict[str, frozenset[str]],
    *,
    highlight_nodes: frozenset[str] | None = None,
    highlight_edges: frozenset[tuple[str, str]] | None = None,
    key: str,
    selectable: bool = True,
    show_minimap: bool = True,
) -> str | None:
    nodes, flow_edges, height, width = build_flow_graph(
        edges,
        screen_regions=_SCREEN_REGIONS,
        tap_edge_keys=_ROUTABLE_EDGE_KEYS,
        dynamic_edge_keys=_DYNAMIC_EDGE_KEYS,
        highlight_nodes=highlight_nodes,
        highlight_edges=highlight_edges,
    )
    return react_flow(
        nodes=nodes,
        edges=flow_edges,
        height=height,
        width=width,
        key=key,
        fit_view=True,
        selectable=selectable,
        show_minimap=show_minimap,
        legend_items=_LEGEND,
    )


def _tap_graph_nodes() -> list[str]:
    nodes: set[str] = set()
    for a, b in _ROUTABLE_EDGE_KEYS:
        nodes.add(str(a))
        nodes.add(str(b))
    return sorted(nodes)


def _plan_bot_route(src: str, dst: str) -> tuple[list[str] | None, str]:
    """Returns (path, mode) where mode is direct or via_main_city."""

    path = bfs_route(src, dst)
    if path is not None:
        return path, "direct"
    hub = "main_city"
    path_to_hub = bfs_route(src, hub)
    path_from_hub = bfs_route(hub, dst)
    if path_to_hub and path_from_hub:
        return path_to_hub + path_from_hub[1:], "via_main_city"
    return None, "direct"


def _route_edge_pairs(path: list[str] | None) -> frozenset[tuple[str, str]]:
    if not path:
        return frozenset()
    return frozenset((a, b) for a, b in itertools.pairwise(path))


def _edge_status(src: str, dst: str) -> str:
    key = (src, dst)
    if key in EDGE_TAPS:
        return "static tap"
    if key in EDGE_DYNAMIC:
        return "dynamic tap"
    return "unknown"


def _edge_action_summary(src: str, dst: str) -> str:
    key = (src, dst)
    if key in EDGE_TAPS:
        return ", ".join(EDGE_TAPS[key])
    if key in EDGE_DYNAMIC:
        spec = EDGE_DYNAMIC[key]
        resolver = str(spec.get("resolver", "?"))
        target = spec.get("target")
        return f"resolver={resolver}" + (f", target={target}" if target is not None else "")
    return ""


def _render_node_details(
    node_id: str | None,
    edges: dict[str, frozenset[str]],
) -> None:
    if not node_id:
        st.caption("Click a node in the graph to inspect its routing edges.")
        return

    outgoing = sorted(edges.get(node_id, frozenset()))
    incoming = sorted(src for src, dsts in edges.items() if node_id in dsts)

    st.markdown(f"**Selected:** `{node_id}`")
    col_in, col_out = st.columns(2)
    col_in.metric("Incoming", len(incoming))
    col_out.metric("Outgoing", len(outgoing))

    rows = [
        {
            "id": f"sel_in_{idx}",
            "dir": "in",
            "edge": f"{src} -> {node_id}",
            "status": _edge_status(src, node_id),
        }
        for idx, src in enumerate(incoming)
    ] + [
        {
            "id": f"sel_out_{idx}",
            "dir": "out",
            "edge": f"{node_id} -> {dst}",
            "status": _edge_status(node_id, dst),
        }
        for idx, dst in enumerate(outgoing)
    ]
    if rows:
        nested_table(
            rows,
            [
                table_column("dir", "dir", width=56),
                table_column("edge", "edge", width=320),
                table_column("status", "status", width=120),
            ],
            height=min(48 + len(rows) * 34, 320),
            striped=True,
            compact=True,
            hide_expand=True,
            key=f"routes_selected_{node_id}",
        )


st.title("Screen routes")

_tap_pairs = set(sorted_edge_pairs(_TAP_GRAPH))

st.caption(
    "Explore the runtime tap graph from `navigation/edge_taps.yaml` and dynamic edge resolvers."
)

metric_a, metric_b, metric_c, metric_d = st.columns(4)
metric_a.metric("Runtime edges", len(_tap_pairs))
metric_b.metric("Static tap edges", len(_STATIC_TAP_EDGE_KEYS))
metric_c.metric("Dynamic tap edges", len(_DYNAMIC_EDGE_KEYS))
metric_d.metric("Screens", len(_tap_graph_nodes()))

tab_graph, tab_edges = st.tabs(["Tap graph", "All edges"])

with tab_graph:
    st.subheader("Bot route planner")
    st.caption("Uses `navigation/screen_graph.py` backed by `navigation/edge_taps.yaml`.")

    nodes = _tap_graph_nodes()
    if not nodes:
        st.info("No tap edges registered yet (`EDGE_TAPS` and `EDGE_DYNAMIC` are empty).")
    else:
        col_a, col_b, col_c = st.columns([1, 1, 1])
        with col_a:
            src = st.selectbox("From screen", nodes, index=0, key="routes_route_from")
        with col_b:
            idx_to = nodes.index("main_city") if "main_city" in nodes else min(1, len(nodes) - 1)
            dst = st.selectbox("To screen", nodes, index=idx_to, key="routes_route_to")
        with col_c:
            focus = st.selectbox(
                "Focus node",
                ["", *nodes],
                index=0,
                key="routes_focus_node",
            )

        path, mode = _plan_bot_route(str(src), str(dst))
        highlight_nodes = frozenset(path or ([focus] if focus else ()))
        highlight_edges = _route_edge_pairs(path)

        selected = _render_flow(
            _TAP_GRAPH,
            highlight_nodes=highlight_nodes,
            highlight_edges=highlight_edges,
            key="flow-tap-graph",
        )
        _render_node_details(selected or (focus if focus else None), _TAP_GRAPH)

        if path is None:
            st.error(f"No bot route found for `{src}` -> `{dst}` (and no route via `main_city`).")
        else:
            if mode == "via_main_city" and src != dst:
                st.info("Showing route **via `main_city`** (hub route).")

            st.markdown("**Planned screen path**")
            st.code(" -> ".join(path), language="text")

            st.markdown("**Per-hop taps**")
            if len(path) <= 1:
                st.caption("Already on target screen.")
            else:
                hop_rows: list[dict[str, Any]] = []
                for i, (a, b) in enumerate(itertools.pairwise(path)):
                    hop_rows.append(
                        {
                            "id": f"routes_hop_{i}",
                            "n": i + 1,
                            "hop": f"{a} -> {b}",
                            "status": _edge_status(a, b),
                            "action": _edge_action_summary(a, b),
                        }
                    )
                nested_table(
                    hop_rows,
                    [
                        table_column("n", "#", width=48, align="right"),
                        table_column("hop", "hop", width=260),
                        table_column("status", "status", width=120),
                        table_column("action", "action", width=520),
                    ],
                    height=min(48 + max(len(hop_rows), 1) * 34, 360),
                    striped=True,
                    compact=True,
                    hide_expand=True,
                    key="routes_planned_hops",
                )

with tab_edges:
    q = (
        st.text_input("Filter edges", placeholder="screen id, status, region...", key="routes_edge_filter")
        .strip()
        .lower()
    )
    wanted_status = st.multiselect(
        "Status",
        ["static tap", "dynamic tap"],
        default=["static tap", "dynamic tap"],
        key="routes_edge_status",
    )
    edge_rows: list[dict[str, Any]] = []
    for idx, (src, dst) in enumerate(sorted_edge_pairs(_TAP_GRAPH)):
        status = _edge_status(src, dst)
        action = _edge_action_summary(src, dst)
        haystack = " ".join((src, dst, status, action)).lower()
        if status not in wanted_status or (q and q not in haystack):
            continue
        edge_rows.append(
            {
                "id": f"routes_edge_{idx}",
                "from": src,
                "to": dst,
                "status": status,
                "action": action,
            }
        )
    with st.expander("Edge table", expanded=True):
        nested_table(
            edge_rows,
            [
                table_column("from", "from", width=180),
                table_column("to", "to", width=180),
                table_column("status", "status", width=120),
                table_column("action", "action", width=420),
            ],
            height=min(48 + max(len(edge_rows), 1) * 34, 560),
            striped=True,
            compact=True,
            hide_expand=True,
            key="routes_edges_nt",
        )
        st.caption(f"Showing **{len(edge_rows)}** of **{len(_tap_pairs)}** runtime edges.")
