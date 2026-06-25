"""Value-greedy research planner.

Decides *which technology to research next* from the static tech tree
(``games/wos/db/research.yaml``), the player's current node levels and Research
Center level. Priority follows the community meta (extra march queue + compounding
speeds first), with value inherited up prerequisite chains. Pure and testable;
the live readers and navigate-and-tap execution are deferred.
"""
from __future__ import annotations

from .model import (
    ResearchGraph,
    ResearchLevel,
    ResearchNode,
    build_graph,
    load_research_graph,
    parse_duration,
)
from .planner import (
    ALL_MAXED,
    NONE,
    RC_GATED,
    SELECTED,
    WA_GATED,
    ResearchCandidate,
    ResearchPlan,
    ResearchRoadmap,
    ResearchStep,
    plan_next,
    research_roadmap,
)
from .policy import (
    BRANCH_ORDER,
    LINE_WEIGHTS,
    base_priority,
    effective_priorities,
)

__all__ = [
    "ALL_MAXED",
    "BRANCH_ORDER",
    "LINE_WEIGHTS",
    "NONE",
    "RC_GATED",
    "SELECTED",
    "WA_GATED",
    "ResearchCandidate",
    "ResearchGraph",
    "ResearchLevel",
    "ResearchNode",
    "ResearchPlan",
    "ResearchRoadmap",
    "ResearchStep",
    "base_priority",
    "build_graph",
    "effective_priorities",
    "load_research_graph",
    "parse_duration",
    "plan_next",
    "research_roadmap",
]
