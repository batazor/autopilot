"""Game screen FSM topology (Python mirror of Go transition tables): regions and edges."""

from __future__ import annotations

import math

import networkx as nx
import pandas as pd
import streamlit as st
from streamlit_react_flow import react_flow

from navigation.fsm_screen_map import (
    FSM_SCREEN_EDGES,
    MAIN_MENU_EDGES,
    TROOPS_EDGES,
    TUNDRA_ADVENTURE_EDGES,
)

_REGIONS: tuple[tuple[str, dict[str, frozenset[str]]], ...] = (
    ("Tundra Adventure", TUNDRA_ADVENTURE_EDGES),
    ("Main Menu", MAIN_MENU_EDGES),
    ("Troops", TROOPS_EDGES),
)

_REGION_BG: dict[str, str] = {
    "Tundra Adventure": "#dbeafe",
    "Main Menu": "#f3e8ff",
    "Troops": "#dcfce7",
    "multi": "#fef9c3",
    "none": "#f4f4f5",
}

# Layout: React Flow positions are top-left; reserve box + gap so labels do not overlap after normalize.
_NODE_STYLE_WIDTH_PX = 240
_SLOT_W = 304.0  # horizontal spacing between node anchors (~240px box + gap)
_SLOT_H = 168.0  # vertical spacing between layers
_NORM_MARGIN = 104.0


def _sorted_edges(edges: dict[str, frozenset[str]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    for src in sorted(edges.keys()):
        for dst in sorted(edges[src]):
            rows.append((src, dst))
    return rows


def _screen_to_regions(
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


def _layout_hierarchical(
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

    G = nx.DiGraph()
    G.add_nodes_from(node_ids)
    G.add_edges_from(pairs)

    if nx.is_directed_acyclic_graph(G):
        generations = list(nx.topological_generations(G))
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
        G.to_undirected(),
        k=spread_k,
        iterations=160,
        seed=42,
        dim=2,
    )
    raw2 = {nid: (float(pos[nid][0]), float(pos[nid][1])) for nid in node_ids if nid in pos}
    return _normalize_xy(raw2, target_w=canvas_w, target_h=canvas_h, margin=norm_margin)


def _edges_to_flow_elements(
    edges: dict[str, frozenset[str]],
    *,
    default_region: str | None = None,
    screen_regions: dict[str, frozenset[str]] | None = None,
) -> tuple[list[dict[str, object]], int, int]:
    """Build React Flow ``elements`` (nodes + edges) and canvas ``height`` / ``width``."""
    node_ids: set[str] = set()
    for s, ds in edges.items():
        node_ids.add(s)
        node_ids.update(ds)
    ordered = sorted(node_ids)
    pairs = _sorted_edges(edges)

    G = nx.DiGraph()
    G.add_nodes_from(ordered)
    G.add_edges_from(pairs)

    if len(ordered) <= 1:
        canvas_w, canvas_h = 1100.0, 420.0
    elif nx.is_directed_acyclic_graph(G):
        gens = list(nx.topological_generations(G))
        max_row = max((len(layer) for layer in gens), default=1)
        n_layers = len(gens)
        canvas_w = max(1120.0, _NORM_MARGIN * 2 + max_row * _SLOT_W)
        canvas_h = max(440.0, _NORM_MARGIN * 2 + n_layers * _SLOT_H)
        canvas_h = float(min(2400, canvas_h))
        canvas_w = float(min(4200, canvas_w))
    else:
        n_g = len(ordered)
        canvas_w = max(1120.0, _NORM_MARGIN * 2 + math.sqrt(n_g) * _SLOT_W * 1.35)
        canvas_h = max(440.0, _NORM_MARGIN * 2 + math.sqrt(n_g) * _SLOT_H * 1.35)
        canvas_h = float(min(2400, canvas_h))
        canvas_w = float(min(4200, canvas_w))

    positions = _layout_hierarchical(
        ordered,
        pairs,
        canvas_w=canvas_w,
        canvas_h=canvas_h,
        slot_w=_SLOT_W,
        slot_h=_SLOT_H,
        norm_margin=_NORM_MARGIN,
    )
    elements: list[dict[str, object]] = []
    for nid in ordered:
        grp = _node_group(nid, default_region=default_region, screen_regions=screen_regions)
        bg = _REGION_BG.get(grp, "#f4f4f5")
        elements.append(
            {
                "id": nid,
                "data": {"label": nid},
                "position": positions[nid],
                "style": {
                    "background": bg,
                    "fontSize": 11,
                    "padding": 8,
                    "borderRadius": 8,
                    "width": _NODE_STYLE_WIDTH_PX,
                    "maxWidth": _NODE_STYLE_WIDTH_PX,
                },
            }
        )
    for i, (src, dst) in enumerate(_sorted_edges(edges)):
        elements.append(
            {
                "id": f"e{i}",
                "source": src,
                "target": dst,
                "animated": False,
            }
        )
    return elements, int(canvas_h), int(canvas_w)


def _render_flow(
    name: str,
    edges: dict[str, frozenset[str]],
    *,
    default_region: str | None = None,
    screen_regions: dict[str, frozenset[str]] | None = None,
    key: str,
) -> None:
    elements, h, w = _edges_to_flow_elements(
        edges,
        default_region=default_region,
        screen_regions=screen_regions,
    )
    react_flow(
        name,
        elements=elements,
        flow_styles={"height": h, "width": w},
        key=key,
    )


st.title("Screen FSM")

_screen_regions = _screen_to_regions(_REGIONS)

tab_regions, tab_merged, tab_edges = st.tabs(["By region", "Merged graph", "All edges"])

with tab_regions:
    for title, edges in _REGIONS:
        slug = "".join(c if c.isalnum() else "_" for c in title.lower())
        n_edges = len(_sorted_edges(edges))
        with st.expander(
            f"{title} — graph & table ({n_edges} edges)",
            expanded=False,
        ):
            _render_flow(
                f"fsm_{slug}",
                edges,
                default_region=title,
                key=f"flow-{slug}",
            )
            df = pd.DataFrame(_sorted_edges(edges), columns=["from", "to"])
            st.dataframe(df, width="stretch", hide_index=True)

with tab_merged:
    st.markdown(
        "Merged graph (`merge_fsm_edges`): node color by region; screens that appear in more than one "
        "region use the **multi** tint."
    )
    with st.expander("Interactive graph", expanded=True):
        _render_flow(
            "fsm_merged",
            FSM_SCREEN_EDGES,
            screen_regions=_screen_regions,
            key="flow-merged",
        )

with tab_edges:
    rows: list[dict[str, str]] = []
    for src, dst in _sorted_edges(FSM_SCREEN_EDGES):
        rf = ", ".join(sorted(_screen_regions.get(src, frozenset())))
        rt = ", ".join(sorted(_screen_regions.get(dst, frozenset())))
        rows.append({"from": src, "to": dst, "regions (from)": rf, "regions (to)": rt})
    with st.expander("Edge table", expanded=True):
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(f"Total edges after merging graphs: **{len(rows)}**.")
