"""Value-greedy research planner: weights, prereq inheritance, RC gating."""
from __future__ import annotations

from games.wos.core.research.planner import (
    ALL_MAXED,
    RC_GATED,
    SELECTED,
    ResearchLevel,
    ResearchNode,
    base_priority,
    build_graph,
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
    # build_graph derives the same-line tier ladder + reverse index, exactly as the
    # file loader does — so tests exercise the real graph wiring.
    return build_graph(
        nodes,
        branch_order=("growth", "battle", "economy"),
        branch_labels={"growth": "Growth", "battle": "Battle", "economy": "Economy"},
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


def test_tier_ladder_gates_on_predecessor_maxed():
    # Same line, two tiers: tier II must wait until tier I is *maxed* (not just
    # researched). The ladder edge is derived from line+tier, not from `requires`.
    g = _graph(
        _node("b1", "bandaging", tier=1, levels=((1, 1), (2, 1))),  # max level = 2
        _node("b2", "bandaging", tier=2, levels=((1, 1),)),
    )
    assert g.tier_predecessor("b2") == "b1"
    # b1 only at level 1 → b2 still locked; planner keeps finishing tier I.
    p = plan_next(g, {"b1": 1}, rc_level=1)
    assert p.step.node_id == "b1"
    assert "b2" not in {c.node_id for c in p.candidates}
    # b1 maxed → tier II unlocks.
    assert plan_next(g, {"b1": 2}, rc_level=1).step.node_id == "b2"


def test_cross_line_prereq_unlocks_at_level_1():
    # A cross-line `requires` only needs the prereq researched (Lv 1+), NOT maxed.
    g = _graph(
        _node("te", "tool_enhancement", levels=((1, 1), (2, 1), (3, 1))),  # max 3
        _node("dep", "close_combat", branch="battle", requires=["te"], levels=((1, 1),)),
    )
    # te at level 1 (not maxed) already unlocks dep.
    p = plan_next(g, {"te": 1}, rc_level=1)
    assert "dep" in {c.node_id for c in p.candidates}
    # te never researched → dep stays locked.
    assert "dep" not in {c.node_id for c in plan_next(g, {}, rc_level=1).candidates}


def test_all_maxed():
    g = _graph(_node("march", "command_tactics", levels=((1, 2),)))
    assert plan_next(g, {"march": 1}, rc_level=2).reason == ALL_MAXED


# --- against the real db/research.yaml ---------------------------------------


def test_real_graph_loads():
    g = load_research_graph()
    assert len(g.nodes) > 200
    assert "command_tactics_i" in g.nodes
    assert {"growth", "economy", "battle"} <= set(g.branch_order)


def test_real_graph_derives_tier_ladder():
    g = load_research_graph()
    # same-line tier chain recovered from line+tier
    assert g.tier_predecessor("bandaging_ii") == "bandaging_i"
    assert g.tier_predecessor("tool_enhancement_vii") == "tool_enhancement_vi"
    assert g.tier_predecessor("bandaging_i") is None
    # ladder folds into value propagation: tier I is worth at least its tier II
    eff = effective_priorities(g)
    assert eff["bandaging_i"] >= eff["bandaging_ii"]


def test_real_fresh_account_high_rc_picks_high_value():
    g = load_research_graph()
    plan = plan_next(g, {}, rc_level=30)
    assert plan.reason == SELECTED
    assert plan.step.priority >= 60          # one of the top-value lines/chains


def test_real_all_maxed_is_all_maxed():
    g = load_research_graph()
    levels = {nid: node.max_level for nid, node in g.nodes.items()}
    assert plan_next(g, levels, rc_level=30).reason == ALL_MAXED
