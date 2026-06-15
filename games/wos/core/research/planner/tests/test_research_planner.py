"""Value-greedy research planner: weights, prereq inheritance, RC gating."""
from __future__ import annotations

from games.wos.core.research.planner import (
    ALL_MAXED,
    RC_GATED,
    SELECTED,
    ResearchGraph,
    ResearchLevel,
    ResearchNode,
    base_priority,
    effective_priorities,
    load_research_graph,
    parse_duration,
    plan_next,
)


def _node(node_id, line, *, branch="growth", tier=1, requires=(), levels=((1, 1),)):
    lvls = tuple(
        ResearchLevel(level=lv, rc=rc, time_s=0, power=None, cost={"meat": 100})
        for lv, rc in levels
    )
    return ResearchNode(node_id, branch, node_id, line, tier, "", tuple(requires), lvls)


def _graph(*nodes):
    children: dict[str, list[str]] = {}
    for n in nodes:
        for r in n.requires:
            children.setdefault(r, []).append(n.id)
    return ResearchGraph(
        nodes={n.id: n for n in nodes},
        branch_order=("growth", "battle", "economy"),
        branch_labels={"growth": "Growth", "battle": "Battle", "economy": "Economy"},
        _children={k: tuple(v) for k, v in children.items()},
    )


# --- helpers / policy --------------------------------------------------------


def test_parse_duration():
    assert parse_duration("00:01:34") == 94
    assert parse_duration("7d") == 604_800
    assert parse_duration("1d 02:00:00") == 86_400 + 7_200


def test_base_priority_ranks_meta():
    march = _node("ct", "command_tactics")
    gather = _node("fg", "food_gathering", branch="economy")
    misc = _node("mx", "mystery_line", branch="economy")
    assert base_priority(march) > base_priority(gather) > base_priority(misc)


def test_effective_priority_inherits_to_prerequisite():
    low = _node("low", "mystery_line")                         # base ~10
    child = _node("child", "tool_enhancement", requires=["low"])  # base ~92
    eff = effective_priorities(_graph(low, child))
    assert eff["low"] >= base_priority(child)                  # lifted by the child
    assert eff["low"] > base_priority(low)


# --- planner -----------------------------------------------------------------


def test_picks_highest_value_researchable():
    g = _graph(
        _node("march", "command_tactics", levels=((1, 2),)),
        _node("gather", "food_gathering", branch="economy", levels=((1, 1),)),
    )
    plan = plan_next(g, {}, rc_level=2)
    assert plan.reason == SELECTED
    assert plan.step.node_id == "march"
    assert {c.node_id for c in plan.candidates} == {"march", "gather"}


def test_rc_gates_higher_value_falls_through_to_affordable():
    g = _graph(
        _node("march", "command_tactics", levels=((1, 2),)),   # needs RC 2
        _node("gather", "food_gathering", branch="economy", levels=((1, 1),)),
    )
    plan = plan_next(g, {}, rc_level=1)
    assert plan.reason == SELECTED
    assert plan.step.node_id == "gather"
    assert "march" in plan.detail                              # notes the gated top pick


def test_rc_gated_when_nothing_researchable():
    g = _graph(_node("march", "command_tactics", levels=((1, 2),)))
    plan = plan_next(g, {}, rc_level=1)
    assert plan.reason == RC_GATED
    assert plan.step is None
    assert "march" in plan.detail


def test_drives_through_prereq_toward_payoff():
    # A low-value tech that unlocks a high-value one is built first (inheritance).
    g = _graph(
        _node("low", "mystery_line", levels=((1, 1),)),
        _node("payoff", "command_tactics", requires=["low"], levels=((1, 1),)),
    )
    first = plan_next(g, {}, rc_level=1)
    assert first.step.node_id == "low"                         # builds the prereq...
    second = plan_next(g, {"low": 1}, rc_level=1)
    assert second.step.node_id == "payoff"                     # ...then the payoff


def test_all_maxed():
    g = _graph(_node("march", "command_tactics", levels=((1, 2),)))
    assert plan_next(g, {"march": 1}, rc_level=2).reason == ALL_MAXED


# --- against the real db/research.yaml ---------------------------------------


def test_real_graph_loads():
    g = load_research_graph()
    assert len(g.nodes) > 200
    assert "command_tactics_i" in g.nodes
    assert {"growth", "economy", "battle"} <= set(g.branch_order)


def test_real_fresh_account_high_rc_picks_high_value():
    g = load_research_graph()
    plan = plan_next(g, {}, rc_level=30)
    assert plan.reason == SELECTED
    assert plan.step.priority >= 60          # one of the top-value lines/chains


def test_real_all_maxed_is_all_maxed():
    g = load_research_graph()
    levels = {nid: node.max_level for nid, node in g.nodes.items()}
    assert plan_next(g, levels, rc_level=30).reason == ALL_MAXED
