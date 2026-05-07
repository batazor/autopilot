"""Screen routing: BFS over FSM topology + tap action registry per directed edge.

Usage pattern
-------------
1. Detect current screen via ``navigation.detector.ScreenDetector``.
2. Call ``route_taps(current, target)`` to get the ordered list of tap sequences.
3. Execute each sequence with a short delay (Navigator uses 0.8 s per tap by default).

Adding a new screen
-------------------
- Add an edge to ``navigation.fsm_screen_map`` (topology only).
- Add ``(src, dst): [Point, ...]`` entries to ``EDGE_TAPS`` below for every direction you need.
- Add detection landmarks to ``navigation.detector._SCREEN_LANDMARKS``.
- Add coordinate constants to ``layout.screens``.
"""

from __future__ import annotations

from collections import deque
from typing import Final

# Tap steps are region names from `area.json` (no hardcoded coordinates).
Tap = str

# ---------------------------------------------------------------------------
# Tap registry
# ---------------------------------------------------------------------------
# Key:   (src_screen_id, dst_screen_id)  — must match ScreenName string values
# Value: ordered list of Points to tap (Navigator inserts 0.8 s delay between taps)
#
# Only directed edges that have a known tap sequence are listed here.
# Edges present in FSM_SCREEN_EDGES but absent here are valid in the topology
# graph yet cannot be traversed by the bot until taps are added.
EDGE_TAPS: Final[dict[tuple[str, str], list[Tap]]] = {
    # main_city → *
    ("main_city", "arena"): ["arena_btn"],
    ("main_city", "training"): ["training_btn"],
    ("main_city", "alliance"): ["alliance_btn"],
    ("main_city", "gathering"): ["world_map_btn"],
    ("main_city", "chief_profile"): ["profile_btn"],
    # * → main_city  (back button)
    ("arena",            "main_city"): ["back_button"],
    ("training",         "main_city"): ["back_button"],
    ("gathering",        "main_city"): ["back_button"],
    ("alliance",         "main_city"): ["back_button"],
    ("chief_profile",    "main_city"): ["back_button"],
}

# ---------------------------------------------------------------------------
# Adjacency graph derived from EDGE_TAPS
# ---------------------------------------------------------------------------
# BFS uses this graph so that IDs always match ScreenName string values.
# FSM_SCREEN_EDGES (navigation.fsm_screen_map) mirrors the Go FSM topology but
# uses Go-style IDs (e.g. "arena_city_view", "main_menu_city") which differ from
# Python ScreenName values — it is kept for visualization only.
_TAPS_GRAPH: dict[str, set[str]] = {}
for _src, _dst in EDGE_TAPS:
    _TAPS_GRAPH.setdefault(_src, set()).add(_dst)


# ---------------------------------------------------------------------------
# BFS path finder
# ---------------------------------------------------------------------------

def bfs_route(src: str, dst: str) -> list[str] | None:
    """Shortest path [src, …, dst] over the tap-action graph; None if unreachable.

    Uses sorted neighbor iteration for deterministic results when multiple
    shortest paths of equal length exist.
    """
    if src == dst:
        return [src]
    visited: set[str] = {src}
    queue: deque[list[str]] = deque([[src]])
    while queue:
        path = queue.popleft()
        for nb in sorted(_TAPS_GRAPH.get(path[-1], set())):
            if nb in visited:
                continue
            new_path = path + [nb]
            if nb == dst:
                return new_path
            visited.add(nb)
            queue.append(new_path)
    return None


def route_taps(src: str, dst: str) -> list[list[Tap]] | None:
    """BFS path from *src* to *dst* resolved to per-hop tap sequences.

    Returns ``None`` when no path exists in the tap-action graph
    (either the edge is unknown or tap coordinates are not yet registered).
    The caller (Navigator) falls back to routing via ``main_city`` in that case.
    """
    path = bfs_route(src, dst)
    if path is None:
        return None
    result: list[list[Tap]] = []
    for a, b in zip(path, path[1:], strict=False):
        taps = EDGE_TAPS.get((a, b))
        if taps is None:
            return None
        result.append(list(taps))
    return result


def reachable_screens(src: str) -> set[str]:
    """All screens reachable from *src* via the tap-action graph (excluding *src*)."""
    visited: set[str] = {src}
    queue: deque[str] = deque([src])
    while queue:
        node = queue.popleft()
        for nb in _TAPS_GRAPH.get(node, set()):
            if nb not in visited:
                visited.add(nb)
                queue.append(nb)
    visited.discard(src)
    return visited
