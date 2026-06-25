"""Optimal allocation: exact, ≥ greedy, with a safe greedy fallback."""
from __future__ import annotations

from games.wos.core.coordinator import (
    CONSTRUCTION,
    MARCH,
    CandidateAction,
    Channel,
    Utility,
    coordinate,
    coordinate_optimal,
)


def _ca(domain, kind, key, priority, cost=None):
    return CandidateAction(domain, kind, key, Utility(base_value=priority), cost or {})


def _value(decision) -> float:
    return sum(c.action.priority for c in decision.commits)


def test_optimal_beats_greedy_when_the_budget_binds():
    """The canonical case: greedy grabs the big item, the optimum takes two small ones."""
    chans = [Channel("c1", CONSTRUCTION), Channel("c2", CONSTRUCTION)]
    a = _ca("d", CONSTRUCTION, "A", 1000.0, {"wood": 100})
    b = _ca("d", CONSTRUCTION, "B", 600.0, {"wood": 50})
    c = _ca("d", CONSTRUCTION, "C", 600.0, {"wood": 50})
    bal = {"wood": 100}

    greedy = coordinate(chans, [a, b, c], bal)
    opt = coordinate_optimal(chans, [a, b, c], bal)

    assert _value(greedy) == 1000.0                       # A alone, B+C starved on wood
    assert _value(opt) == 1200.0                          # B+C is the optimum
    assert {x.action.key for x in opt.commits} == {"B", "C"}
    assert "wood" in opt.bottleneck_resources             # A couldn't fit → bottleneck signal kept
    assert any(s.key == "A" for s in opt.starved)


def test_matches_greedy_when_nothing_contends():
    chans = [Channel("c1", CONSTRUCTION), Channel("r1", "research")]
    cands = [
        _ca("building_progression", CONSTRUCTION, "furnace", 850.0, {}),
        _ca("research", "research", "tech", 900.0, {"wood": 10}),
    ]
    bal = {"wood": 1000}
    opt = coordinate_optimal(chans, cands, bal)
    assert {x.action.key for x in opt.commits} == {"furnace", "tech"}   # both fit, both run
    assert _value(opt) == _value(coordinate(chans, cands, bal))


def test_march_stamina_knapsack_picks_the_best_affordable_set():
    """Three markers, two lanes, stamina for two — take the two highest-value."""
    chans = [Channel("m1", MARCH), Channel("m2", MARCH)]
    hi = _ca("intel", MARCH, "hi", 760.0, {"stamina": 10})
    mid = _ca("intel", MARCH, "mid", 700.0, {"stamina": 10})
    lo = _ca("intel", MARCH, "lo", 600.0, {"stamina": 10})
    opt = coordinate_optimal(chans, [hi, mid, lo], {"stamina": 20})
    assert {x.action.key for x in opt.commits} == {"hi", "mid"}
    assert opt.remaining["stamina"] == 0
    assert any(s.key == "lo" for s in opt.starved)


def test_multi_resource_budget():
    chans = [Channel("c1", CONSTRUCTION), Channel("c2", CONSTRUCTION)]
    a = _ca("d", CONSTRUCTION, "A", 500.0, {"wood": 80, "iron": 10})
    b = _ca("d", CONSTRUCTION, "B", 500.0, {"wood": 80, "iron": 10})
    opt = coordinate_optimal(chans, [a, b], {"wood": 100, "iron": 100})
    assert len(opt.commits) == 1                          # only one fits within 100 wood
    assert "wood" in opt.bottleneck_resources


def test_candidates_without_a_channel_are_reported():
    chans = [Channel("c1", CONSTRUCTION)]
    on = _ca("building_progression", CONSTRUCTION, "furnace", 850.0, {})
    off = _ca("heroes", "hero", "natalia", 580.0, {})
    opt = coordinate_optimal(chans, [on, off], {})
    assert [x.action.key for x in opt.commits] == ["furnace"]
    assert [x.key for x in opt.no_channel] == ["natalia"]


def test_falls_back_to_greedy_on_node_overflow():
    """With a starved node budget it returns exactly the greedy decision."""
    chans = [Channel("c1", CONSTRUCTION), Channel("c2", CONSTRUCTION)]
    cands = [
        _ca("d", CONSTRUCTION, k, p, {"wood": 50})
        for k, p in [("A", 1000.0), ("B", 600.0), ("C", 600.0)]
    ]
    bal = {"wood": 100}
    fell_back = coordinate_optimal(chans, cands, bal, max_nodes=1)
    greedy = coordinate(chans, cands, bal)
    assert [c.action.key for c in fell_back.commits] == [c.action.key for c in greedy.commits]


def test_empty_inputs():
    assert coordinate_optimal([], [], {}).commits == ()
    chans = [Channel("c1", CONSTRUCTION)]
    assert coordinate_optimal(chans, [], {}).commits == ()
