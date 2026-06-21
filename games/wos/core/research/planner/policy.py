"""Value weighting for the research planner — "what gives the biggest payoff".

The priority of a tech is driven by community meta, not raw power. The weights
below are config-as-code (edit here); they encode the consensus from the WoS
research guides:

* Extra march queue (``command_tactics``) — top: more simultaneous gathering /
  intel / rallies. Directly raises march-slot capacity in the resource world.
* Compounding speeds first — ``tool_enhancement`` (research speed) and
  ``tooling_up`` (construction speed) pay back on every later upgrade.
* Then army size + infantry attack (``regimental_expansion`` / ``reprisal_tactics``
  / ``close_combat``), then gathering economy, then training / survivability.

Sources: whiteoutsurvival.wiki/research, veloxgame & ldshop research guides.

Key refinement — :func:`effective_priorities` propagates each node's weight UP to
its prerequisites: a low-value tech that *unlocks* a high-value one inherits that
value. So value-greedy selection naturally walks prerequisite chains toward the
biggest payoff without the caller naming explicit targets.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from games.wos.core.roles import multiplier as role_multiplier

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from games.wos.core.roles import RoleProfile

    from .model import ResearchGraph, ResearchNode

# Weight by node *line* (family, roman numeral stripped). Higher = research sooner.
LINE_WEIGHTS: dict[str, float] = {
    "command_tactics": 100.0,        # +1 march queue (expedition army)
    "tool_enhancement": 92.0,        # research speed — compounds
    "tooling_up": 84.0,              # construction speed — compounds
    "regimental_expansion": 72.0,    # march / army size
    "reprisal_tactics": 64.0,        # infantry attack
    "close_combat": 60.0,            # infantry lethality
    "food_gathering": 50.0,          # economy throughput
    "wood_gathering": 50.0,
    "coal_mining": 48.0,
    "iron_mining": 48.0,
    "trainer_tools": 44.0,           # troop training speed
    "camp_expansion": 42.0,          # training capacity
    "survival_techniques": 36.0,     # all-troop health
    "shield_upgrade": 34.0,          # infantry health
    "defensive_formation": 32.0,
    "special_defensive_training": 30.0,
    "bandaging": 28.0,               # healing speed
    "ward_expansion": 26.0,          # infirmary capacity
    "marksman_armor": 24.0,
    "lancer_armor": 22.0,
}

# Tie-break / fallback when a line isn't in the table: Growth → Battle → Economy.
BRANCH_ORDER: dict[str, float] = {"growth": 3.0, "battle": 2.0, "economy": 1.0}
DEFAULT_WEIGHT = 10.0

# Keyword fallback for unlisted lines (e.g. T11/T12 troop branches).
_KEYWORD_WEIGHTS = (
    ("march", 70.0), ("expedition", 70.0), ("research speed", 90.0),
    ("construction", 82.0), ("attack", 62.0), ("lethality", 60.0),
    ("gather", 50.0), ("training", 44.0), ("health", 34.0), ("defense", 32.0),
)


def base_priority(
    node: ResearchNode,
    weights: Mapping[str, float] | None = None,
    role: RoleProfile | None = None,
) -> float:
    """A node's own value (before prerequisite inheritance).

    When a ``role`` is given, its category multiplier biases the weight (farm
    lifts economy, fighter lifts battle); Growth is ×1.0 for every role so the
    universal-profit techs (march queue, compounding speeds) are never demoted.
    """
    table = weights if weights is not None else LINE_WEIGHTS
    if node.line in table:
        base = table[node.line]
    else:
        bonus = node.bonus.lower()
        base = next((w for kw, w in _KEYWORD_WEIGHTS if kw in bonus), DEFAULT_WEIGHT)
    if role is not None:
        base *= role_multiplier(role, node.branch)
    # tiny branch nudge so equal-weight lines still order Growth > Battle > Economy
    return base + BRANCH_ORDER.get(node.branch, 0.0) * 0.001


def effective_priorities(
    graph: ResearchGraph,
    base_fn: Callable[[ResearchNode], float] | None = None,
) -> dict[str, float]:
    """Each node's value lifted to the max of itself and anything it unlocks.

    So a prerequisite is at least as important as its most valuable descendant —
    value-greedy then drives through prereq chains toward the biggest payoff.
    """
    base = base_fn or base_priority
    memo: dict[str, float] = {}

    def eff(node_id: str, stack: frozenset[str]) -> float:
        if node_id in memo:
            return memo[node_id]
        node = graph.spec(node_id)
        if node is None:
            return 0.0
        if node_id in stack:                      # cycle guard
            return base(node)
        val = base(node)
        for child in graph.children(node_id):
            val = max(val, eff(child, stack | {node_id}))
        memo[node_id] = val
        return val

    return {nid: eff(nid, frozenset()) for nid in graph.nodes}
