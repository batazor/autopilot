"""Tests for the research roadmap aggregator (total cost/time/power, current→target)."""
from __future__ import annotations

from games.wos.core.research.planner import (
    ResearchLevel,
    ResearchNode,
    build_graph,
    load_research_graph,
    research_roadmap,
)


def _synthetic_graph():
    levels = (
        ResearchLevel(level=1, rc=1, time_s=100, power=1, cost={"meat": 10}),
        ResearchLevel(level=2, rc=2, time_s=200, power=2, cost={"meat": 20}),
        ResearchLevel(level=3, rc=3, time_s=400, power=3, cost={"meat": 40}),
    )
    node = ResearchNode(
        id="n", branch="b", name="N", line="n", tier=1, bonus="", requires=(), levels=levels,
    )
    return build_graph([node], ["b"], {"b": "B"})


def test_roadmap_totals_and_speed() -> None:
    g = _synthetic_graph()
    rm = research_roadmap(g, {"n": 0}, {"n": 3})
    assert rm.cost == {"meat": 70}
    assert rm.total_cost == 70
    assert rm.power_gain == 6        # 1 + 2 + 3
    assert rm.steps == 3
    assert rm.time_s == 700          # 100 + 200 + 400
    assert rm.missing == ()
    # +100% research speed halves the wall-clock time.
    rm_fast = research_roadmap(g, {"n": 0}, {"n": 3}, research_speed_pct=100)
    assert rm_fast.time_s == 700
    assert rm_fast.time_s_adjusted == 350


def test_roadmap_clamps_and_skips_below_current() -> None:
    g = _synthetic_graph()
    # current 2 → target 5 (clamped to max 3): only level 3 counts.
    rm = research_roadmap(g, {"n": 2}, {"n": 5})
    assert rm.cost == {"meat": 40}
    assert rm.steps == 1
    assert rm.time_s == 400
    # target ≤ current contributes nothing.
    assert research_roadmap(g, {"n": 3}, {"n": 2}).steps == 0


def test_roadmap_reports_missing_nodes() -> None:
    g = _synthetic_graph()
    rm = research_roadmap(g, {}, {"nope": 2})
    assert rm.missing == ("nope",)
    assert rm.total_cost == 0
    assert rm.steps == 0


def test_roadmap_real_data_anchor() -> None:
    # Ties the aggregator to the actual research.yaml: Bandaging I levels 1-3
    # meat costs are 6700 + 9400 + 20000 = 36100.
    g = load_research_graph()
    rm = research_roadmap(g, {"bandaging_i": 0}, {"bandaging_i": 3})
    assert rm.cost["meat"] == 36100
    assert rm.steps == 3
    assert rm.time_s > 0
