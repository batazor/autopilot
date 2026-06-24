"""War Academy FC gate on the T11/T12 troop research — separate from the RC."""
from __future__ import annotations

from games.wos.core.research.planner import (
    WA_GATED,
    ResearchLevel,
    ResearchNode,
    build_graph,
    load_research_graph,
    plan_next,
)
from games.wos.core.research.planner.model import _war_academy_fc


def _node(node_id, *, tier, levels):
    return ResearchNode(node_id, "battle", node_id, node_id, tier, "", (), tuple(levels))


def _graph(*nodes):
    return build_graph(
        nodes,
        branch_order=("growth", "battle", "economy"),
        branch_labels={"growth": "Growth", "battle": "Battle", "economy": "Economy"},
    )


# A WA-gated Helios level as the loader produces it: rc is folded to 35 (FC5) AND the
# distinct war_academy_fc=5 is kept. A plain RC tech sits alongside it.
WA = ResearchLevel(level=1, rc=35, time_s=0, power=8_000_000, cost={"fc_shards": 100},
                   war_academy_fc=5)
RC = ResearchLevel(level=1, rc=10, time_s=0, power=100, cost={"meat": 100})
G = _graph(_node("helios_x", tier=11, levels=(WA,)), _node("econ", tier=1, levels=(RC,)))


# --- parsing ------------------------------------------------------------------
def test_gate_parses_to_war_academy_fc():
    assert _war_academy_fc({"gate": "FC5"}) == 5
    assert _war_academy_fc({"gate": "FC-10"}) == 10
    assert _war_academy_fc({"rc": 30}) == 0          # a real RC gate → no WA requirement
    assert _war_academy_fc({}) == 0


def test_real_helios_node_carries_the_gate():
    helios = load_research_graph().spec("helios_infantry")
    assert helios is not None
    assert helios.level_at(1).war_academy_fc == 5    # gate: "FC5" in research.yaml


# --- enforcement (the calculator's War Academy FC check) ----------------------
def _ids(plan):
    return {c.node_id for c in plan.candidates}


def test_wa_gate_blocks_until_the_war_academy_is_high_enough():
    # War Academy FC4 < 5 → Helios is gated out even though RC (30) clears the fold.
    assert "helios_x" not in _ids(plan_next(G, {}, rc_level=30, war_academy_fc=4))
    # FC5 → researchable, and notably *despite* RC 30 < the folded 35 (WA gate replaces it).
    assert "helios_x" in _ids(plan_next(G, {}, rc_level=30, war_academy_fc=5))


def test_only_wa_tech_left_reports_wa_gated_with_note():
    only_wa = _graph(_node("helios_x", tier=11, levels=(WA,)))
    plan = plan_next(only_wa, {}, rc_level=99, war_academy_fc=2)
    assert plan.reason == WA_GATED
    assert "War Academy FC5" in plan.detail


def test_none_default_preserves_the_rc_fold():
    # war_academy_fc=None → the WA tech falls back to the RC fold (rc 35), unchanged.
    assert "helios_x" not in _ids(plan_next(G, {}, rc_level=30))   # 30 < 35 → blocked
    assert "helios_x" in _ids(plan_next(G, {}, rc_level=35))       # 35 ≥ 35 → researchable
