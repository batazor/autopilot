"""Per-edge ``cond`` gating in the screen-routing graph.

An edge in ``edge_taps.yaml`` may carry a ``cond`` (the same ``eval_cond``
mini-language used by DSL ``cond`` / area versions). The loader records the cond
in a parallel registry — the taps themselves stay in the static graph, so the
unfiltered topology is unchanged — and ``bfs_route`` accepts an ``edge_allowed``
predicate that prunes cond-failing edges so the search routes around them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navigation import screen_graph

if TYPE_CHECKING:
    from pathlib import Path


def _isolate(mocker: Any, cfg: Path) -> None:
    mocker.patch.object(
        screen_graph, "_edge_taps_yaml_paths", new=lambda **_: [cfg]
    )
    mocker.patch.object(screen_graph, "_hero_ids", new=list)
    mocker.patch.object(
        screen_graph, "_building_screen_defs", new=lambda *_a, **_k: []
    )
    screen_graph.invalidate_edge_taps_cache()


def test_load_edge_taps_parses_cond_and_strips_it_from_specs(
    mocker: Any, tmp_path: Path
) -> None:
    cfg = tmp_path / "edge_taps.yaml"
    cfg.write_text(
        """
edges:
  a:
    b:
      taps: [a.to.b]
      cond: "buildings.levels.furnace >= 10"
    c: [a.to.c]
  c:
    d:
      resolver: some_resolver
      target: x
      cond: "vip.level >= 1"
""",
        encoding="utf-8",
    )
    _isolate(mocker, cfg)
    try:
        static, dynamic, conds = screen_graph._load_edge_taps(game="t")
        # dict-form static edge → taps land in static, cond in the cond registry.
        assert static[("a", "b")] == ["a.to.b"]
        assert conds[("a", "b")] == "buildings.levels.furnace >= 10"
        # plain list edge → no cond recorded.
        assert static[("a", "c")] == ["a.to.c"]
        assert ("a", "c") not in conds
        # dynamic edge → resolver spec retains its keys but NOT cond (popped so
        # the resolver never sees it); cond goes to the registry.
        assert dynamic[("c", "d")] == {"resolver": "some_resolver", "target": "x"}
        assert conds[("c", "d")] == "vip.level >= 1"
    finally:
        screen_graph.invalidate_edge_taps_cache()


def test_bfs_routes_around_cond_failed_edge(mocker: Any, tmp_path: Path) -> None:
    # Two 2-hop routes a -> d: via b (furnace-gated) and via c (always open).
    cfg = tmp_path / "edge_taps.yaml"
    cfg.write_text(
        """
edges:
  a:
    b:
      taps: [a.to.b]
      cond: "furnace >= 10"
    c: [a.to.c]
  b:
    d: [b.to.d]
  c:
    d: [c.to.d]
""",
        encoding="utf-8",
    )
    _isolate(mocker, cfg)
    try:
        from dsl.cond_eval import eval_cond

        conds = screen_graph.edge_conds_for_game("t")

        def allowed(flat: dict[str, Any]) -> Any:
            def predicate(src: str, dst: str) -> bool:
                cond = conds.get((src, dst))
                return cond is None or eval_cond(cond, flat)

            return predicate

        # cond fails (and the unknown-state case) → prune b, route around via c.
        assert screen_graph.bfs_route(
            "a", "d", game="t", edge_allowed=allowed({"furnace": 1})
        ) == ["a", "c", "d"]
        assert screen_graph.bfs_route(
            "a", "d", game="t", edge_allowed=allowed({})
        ) == ["a", "c", "d"]
        # cond passes → the gated branch is available again (tie broken to 'b').
        assert screen_graph.bfs_route(
            "a", "d", game="t", edge_allowed=allowed({"furnace": 10})
        ) == ["a", "b", "d"]
        # No predicate → full static topology, unchanged.
        assert screen_graph.bfs_route("a", "d", game="t") == ["a", "b", "d"]
    finally:
        screen_graph.invalidate_edge_taps_cache()


def test_node_cond_expands_onto_every_incident_edge(
    mocker: Any, tmp_path: Path
) -> None:
    # A node-level cond is sugar for "this screen exists only when the cond
    # holds": it folds onto the cond of every edge touching the node (entries,
    # exits, and back edges), AND-ed with any edge-level cond.
    cfg = tmp_path / "edge_taps.yaml"
    cfg.write_text(
        """
nodes:
  m:
    cond: "furnace >= 10"
edges:
  a:
    m: [a.to.m]
    x: [a.to.x]
  m:
    b: [m.to.b]
  b:
    m:
      taps: [b.back.m]
      cond: "vip >= 1"
""",
        encoding="utf-8",
    )
    _isolate(mocker, cfg)
    try:
        _static, _dynamic, conds = screen_graph._load_edge_taps(game="t")
        # Lands on every edge incident to 'm' (entry, exit, back edge).
        assert conds[("a", "m")] == "(furnace >= 10)"
        assert conds[("m", "b")] == "(furnace >= 10)"
        # AND-combined with a pre-existing edge cond (edge cond first).
        assert conds[("b", "m")] == "(vip >= 1) and (furnace >= 10)"
        # An edge that doesn't touch 'm' is left alone.
        assert ("a", "x") not in conds
    finally:
        screen_graph.invalidate_edge_taps_cache()
