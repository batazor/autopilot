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
WA_GATED = "wa_gated"          # top tech is a T11/T12 troop tech needing a higher War Academy
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


def _gate_note(block: tuple[str, float, str, int], *, rc_level: int, wa_fc: int, lead: str) -> str:
    """Wording for a blocked tech — RC kind ``("rc", …)`` vs War Academy ``("wa", …)``."""
    kind, _prio, node_id, needed = block
    if kind == "wa":
        return f"{lead} {node_id} needs War Academy FC{needed} (you have {wa_fc})"
    return f"{lead} {node_id} needs Research Center Lv {needed} (you have {rc_level})"


def plan_next(
    graph: ResearchGraph,
    levels: Mapping[str, int],
    rc_level: int,
    *,
    war_academy_fc: int | None = None,
    weights: Mapping[str, float] | None = None,
    role: RoleProfile | None = None,
) -> ResearchPlan:
    """Pick the next tech to research under the value-greedy meta policy.

    ``levels`` maps ``node_id`` → current level (0 = not researched). ``rc_level``
    is the Research Center building level. ``war_academy_fc`` is the War Academy's FC
    level: when given, the T11/T12 troop techs (``gate: "FCx"``) are gated on *it*
    (a building independent of the RC) and surface ``wa_gated``; when ``None`` (the
    default) they fall back to the RC fold, unchanged. ``role`` biases the weights.
    Returns the highest-value researchable tech, or ``rc_gated`` / ``wa_gated`` /
    ``all_maxed`` when nothing is researchable now.
    """
    eff = effective_priorities(graph, lambda n: base_priority(n, weights, role))

    best = None                # (node, next_level, sort_key)
    best_key = None
    # Highest-value tech we can't reach yet, by gate kind: ("rc"|"wa", prio, node_id, needed).
    rc_blocked_top: tuple[str, float, str, int] | None = None
    wa_blocked_top: tuple[str, float, str, int] | None = None
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
        wa_gated = war_academy_fc is not None and nxt.war_academy_fc > 0
        researchable = (war_academy_fc >= nxt.war_academy_fc) if wa_gated else (rc_level >= nxt.rc)
        if researchable:
            ranked.append(ResearchCandidate(node_id, node.name, prio, nxt.level))
            key = (prio, -node.tier, -nxt.total_cost)
            if best_key is None or key > best_key:
                best, best_key = (node, nxt, cur), key
        elif wa_gated:
            if wa_blocked_top is None or prio > wa_blocked_top[1]:
                wa_blocked_top = ("wa", prio, node_id, nxt.war_academy_fc)
        elif rc_blocked_top is None or prio > rc_blocked_top[1]:
            rc_blocked_top = ("rc", prio, node_id, nxt.rc)

    ranked.sort(key=lambda c: (-c.priority, c.node_id))
    ranked_t = tuple(ranked[:6])

    blockers = [b for b in (rc_blocked_top, wa_blocked_top) if b is not None]
    top_block = max(blockers, key=lambda b: b[1]) if blockers else None

    if best is not None:
        node, nxt, cur = best
        detail = ""
        if top_block is not None and top_block[1] > best_key[0]:
            detail = _gate_note(top_block, rc_level=rc_level,
                                wa_fc=war_academy_fc or 0, lead="higher-value")
        step = ResearchStep(
            node_id=node.id, branch=node.branch, name=node.name, line=node.line,
            from_level=cur, to_level=nxt.level, rc_required=nxt.rc, priority=best_key[0],
        )
        return ResearchPlan(step, SELECTED, detail, ranked_t)

    if top_block is not None:
        reason = WA_GATED if top_block[0] == "wa" else RC_GATED
        return ResearchPlan(
            None, reason,
            _gate_note(top_block, rc_level=rc_level, wa_fc=war_academy_fc or 0,
                       lead="top researchable tech"),
            ranked_t,
        )
    # nothing researchable now: everything maxed, or unmaxed but prereq-locked
    return ResearchPlan(None, ALL_MAXED if not any_unmaxed else NONE)
