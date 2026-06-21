"""Role bias on the value-greedy research planner."""
from __future__ import annotations

from games.wos.core.research.planner import (
    SELECTED,
    ResearchGraph,
    ResearchLevel,
    ResearchNode,
    base_priority,
    plan_next,
)
from games.wos.core.roles import get_role


def _node(node_id, line, *, branch, max_level=3):
    lvls = tuple(
        ResearchLevel(level=lv, rc=1, time_s=0, power=None, cost={"meat": 100})
        for lv in range(1, max_level + 1)
    )
    return ResearchNode(node_id, branch, node_id, line, 1, "", (), lvls)


def _graph(*nodes):
    return ResearchGraph(
        nodes={n.id: n for n in nodes},
        branch_order=("growth", "battle", "economy"),
        branch_labels={"growth": "Growth", "battle": "Battle", "economy": "Economy"},
        _children={},
    )


GATHER = _node("gather", "food_gathering", branch="economy")
ATTACK = _node("attack", "reprisal_tactics", branch="battle")
MARCH = _node("march", "command_tactics", branch="growth", max_level=1)
G = _graph(GATHER, ATTACK, MARCH)


def test_growth_stays_top_regardless_of_role():
    for role_id in ("balanced", "farm", "fighter"):
        plan = plan_next(G, {}, rc_level=5, role=get_role(role_id))
        assert plan.reason == SELECTED
        assert plan.step.node_id == "march"        # command_tactics wins for everyone


def test_farm_prefers_economy_over_battle():
    # March maxed → choice is economy vs battle.
    plan = plan_next(G, {"march": 1}, rc_level=5, role=get_role("farm"))
    assert plan.step.node_id == "gather"


def test_fighter_prefers_battle_over_economy():
    plan = plan_next(G, {"march": 1}, rc_level=5, role=get_role("fighter"))
    assert plan.step.node_id == "attack"


def test_balanced_default_uses_raw_weights():
    # Without a role, raw weights apply (reprisal 64 > food 50).
    plan = plan_next(G, {"march": 1}, rc_level=5)
    assert plan.step.node_id == "attack"


def test_role_scales_base_priority():
    # Farm suppresses battle (economy kept full), flipping the eco-vs-battle order.
    farm = get_role("farm")
    assert base_priority(ATTACK, role=farm) < base_priority(ATTACK)
    assert base_priority(GATHER, role=farm) == base_priority(GATHER)   # economy untouched
    assert base_priority(GATHER, role=farm) > base_priority(ATTACK, role=farm)
