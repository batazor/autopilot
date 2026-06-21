"""Value-greedy research planner: decide which tech to research next.

Pure decision function over the static :class:`~model.ResearchGraph`, the
player's current node levels, and their Research Center level. Among the techs
that are *researchable right now* (prerequisites satisfied AND RC level high
enough), it picks the highest effective value (see :mod:`policy` — meta weights
with prerequisite inheritance), tie-breaking by lower tier then cheaper.

"Prerequisites satisfied" follows the in-game tier ladder (see :func:`_prereqs_satisfied`):
the same-line previous tier must be **maxed**, while a cross-line ``requires`` only
needs to be **unlocked** (Lv 1+). RC building level gates each individual level.

When the single most valuable line is blocked only by the Research Center level,
that's surfaced (``rc_gated`` / a note on the pick) so the bot knows raising RC /
the Furnace unlocks it — tying research progress back to the building planner.

The live readers (current tech levels, RC level) and navigate-and-tap execution
are deferred; this module only answers "what next?".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .policy import base_priority, effective_priorities

if TYPE_CHECKING:
    from collections.abc import Mapping

    from games.wos.core.roles import RoleProfile

    from .model import ResearchGraph, ResearchNode

# --- Plan reasons ------------------------------------------------------------
SELECTED = "selected"          # step is the tech to research now
RC_GATED = "rc_gated"          # researchable techs exist but all need a higher RC
ALL_MAXED = "all_maxed"        # every reachable tech is maxed
NONE = "none"                  # nothing to do (empty graph)


@dataclass(frozen=True, slots=True)
class ResearchStep:
    """The single tech upgrade the planner picked this pass."""

    node_id: str
    branch: str
    name: str
    line: str
    from_level: int
    to_level: int
    rc_required: int
    priority: float


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    """A ranked researchable-now option (for the decision trace)."""

    node_id: str
    name: str
    priority: float
    to_level: int


@dataclass(frozen=True, slots=True)
class ResearchPlan:
    step: ResearchStep | None
    reason: str
    detail: str = ""
    candidates: tuple[ResearchCandidate, ...] = field(default_factory=tuple)


# Unlock thresholds for the two kinds of prerequisite edge. Isolated here so the
# rule is easy to retune: the same-line tier ladder gates on the predecessor being
# fully maxed, cross-line arrows only need the prereq researched (Lv 1+).
_CROSS_LINE_UNLOCK = 1


def _prereqs_satisfied(
    graph: ResearchGraph, node: ResearchNode, levels: Mapping[str, int]
) -> bool:
    """In-game unlock rule for ``node`` given current ``levels``.

    * same-line previous tier (the tier ladder) must be **maxed**;
    * each cross-line ``requires`` must be **unlocked** (``>= _CROSS_LINE_UNLOCK``).

    Unknown dependencies never hard-block (defensive against partial data).
    """
    pred = graph.tier_predecessor(node.id)
    if pred is not None:
        spec = graph.spec(pred)
        if spec is not None and int(levels.get(pred, 0)) < spec.max_level:
            return False
    for req in node.requires:
        if graph.spec(req) is None:
            continue                                  # unknown dep → don't hard-block
        if int(levels.get(req, 0)) < _CROSS_LINE_UNLOCK:
            return False
    return True


def plan_next(
    graph: ResearchGraph,
    levels: Mapping[str, int],
    rc_level: int,
    *,
    weights: Mapping[str, float] | None = None,
    role: RoleProfile | None = None,
) -> ResearchPlan:
    """Pick the next tech to research under the value-greedy meta policy.

    ``levels`` maps ``node_id`` → current level (0 = not researched). ``rc_level``
    is the Research Center building level. ``role`` biases the weights toward the
    account's purpose (farm → economy, fighter → battle; Growth stays universal).
    Returns the highest-value researchable tech, or ``rc_gated`` / ``all_maxed``
    when nothing is researchable now.
    """
    eff = effective_priorities(graph, lambda n: base_priority(n, weights, role))

    best = None                # (node, next_level, sort_key)
    best_key = None
    rc_blocked_top: tuple[float, str, int] | None = None   # (prio, node_id, rc_needed)
    ranked: list[ResearchCandidate] = []
    any_unmaxed = False

    for node_id in sorted(graph.nodes):
        node = graph.nodes[node_id]
        cur = int(levels.get(node_id, 0))
        if cur >= node.max_level:
            continue
        any_unmaxed = True
        nxt = node.next_after(cur)
        if nxt is None or not _prereqs_satisfied(graph, node, levels):
            continue
        prio = eff[node_id]
        if rc_level >= nxt.rc:
            ranked.append(ResearchCandidate(node_id, node.name, prio, nxt.level))
            key = (prio, -node.tier, -nxt.total_cost)
            if best_key is None or key > best_key:
                best, best_key = (node, nxt, cur), key
        elif rc_blocked_top is None or prio > rc_blocked_top[0]:
            rc_blocked_top = (prio, node_id, nxt.rc)

    ranked.sort(key=lambda c: (-c.priority, c.node_id))
    ranked_t = tuple(ranked[:6])

    if best is not None:
        node, nxt, cur = best
        detail = ""
        if rc_blocked_top and rc_blocked_top[0] > best_key[0]:
            detail = (
                f"higher-value {rc_blocked_top[1]} needs Research Center "
                f"Lv {rc_blocked_top[2]} (you have {rc_level})"
            )
        step = ResearchStep(
            node_id=node.id, branch=node.branch, name=node.name, line=node.line,
            from_level=cur, to_level=nxt.level, rc_required=nxt.rc, priority=best_key[0],
        )
        return ResearchPlan(step, SELECTED, detail, ranked_t)

    if rc_blocked_top is not None:
        return ResearchPlan(
            None, RC_GATED,
            f"top researchable tech {rc_blocked_top[1]} needs Research Center "
            f"Lv {rc_blocked_top[2]} (you have {rc_level})",
            ranked_t,
        )
    # nothing researchable now: everything maxed, or unmaxed but prereq-locked
    return ResearchPlan(None, ALL_MAXED if not any_unmaxed else NONE)
