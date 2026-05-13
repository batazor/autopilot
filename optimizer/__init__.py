"""Upgrade-action optimizer.

Two public entry points share the same pipeline:

* :func:`rank_candidates` — score every candidate, return a sorted list
  with breakdowns. Doesn't allocate budgets — useful for debug / dry-run.
* :func:`solve_optimal` — same up to scoring, then CP-SAT picks the
  subset that maximises Σ score under the spendable capacities (and the
  hard rules pre-pruned out of consideration). Production path.

Layout::

    optimizer/types.py        — dataclasses: Candidate, ScoreBreakdown
    optimizer/context.py      — load balance configs into one bundle
    optimizer/candidates.py   — generate candidates from state
    optimizer/scorer.py       — float score with breakdown
    optimizer/hard_rules.py   — drop unsafe candidates *before* the solver
    optimizer/capacities.py   — extract spendable amounts from state
    optimizer/solver.py       — CP-SAT subset selection
"""

from __future__ import annotations

from optimizer.candidates import generate_candidates
from optimizer.capacities import compute_capacities
from optimizer.context import BalanceContext, load_balance_context
from optimizer.dispatcher import (
    TaskEnvelope,
    build_envelope,
    enqueue_envelope,
    envelope_to_redis_payload,
    queue_key,
    scenario_name_for,
)
from optimizer.hard_rules import PruneResult, prune_candidates
from optimizer.history import HistoryEntry, append_entry, load_history, now_ts
from optimizer.reasons import generate_reasons, rejection_reason
from optimizer.scorer import ScoreBreakdown, score_candidate
from optimizer.solver import (
    ORToolsUpgradeOptimizer,
    SolverResult,
    solve_with_context,
    to_int_score,
)
from optimizer.types import Candidate, Cost

# executor depends on the symbols above, so import last to dodge a cycle.
# ``# isort: split`` tells ruff/isort to treat this as a separate import block
# and not lift it to the top of the file.
# isort: split
from optimizer.executor import PlanStep, apply_command, plan_top_k  # noqa: E402


def rank_candidates(
    state_flat: dict[str, object],
    context: BalanceContext | None = None,
    *,
    server_age_days: int = 0,
) -> list[tuple[Candidate, ScoreBreakdown]]:
    """Score every candidate, sort by ``final_score`` desc."""
    ctx = context or load_balance_context()
    cands = generate_candidates(state_flat, ctx)
    scored: list[tuple[Candidate, ScoreBreakdown]] = []
    for c in cands:
        br = score_candidate(c, ctx, state_flat, server_age_days=server_age_days)
        scored.append((c, br))
    scored.sort(key=lambda pair: pair[1].final_score, reverse=True)
    return scored


def solve_optimal(
    state_flat: dict[str, object],
    context: BalanceContext | None = None,
    *,
    server_age_days: int = 0,
    batch: bool = False,
) -> tuple[SolverResult, PruneResult, dict[str, ScoreBreakdown]]:
    """Full pipeline: candidates → prune by hard rules → score → CP-SAT.

    Returns the solver verdict plus the prune report and per-id score
    breakdowns, so UI/debug surfaces don't have to re-run the upstream
    stages. ``batch=True`` switches solver params from the ``online``
    block to the ``batch`` block in ``defaults.solver``.
    """
    ctx = context or load_balance_context()
    candidates = generate_candidates(state_flat, ctx)
    prune = prune_candidates(candidates, state_flat, ctx)
    breakdowns: dict[str, ScoreBreakdown] = {}
    for c in prune.kept:
        breakdowns[c.id] = score_candidate(
            c, ctx, state_flat, server_age_days=server_age_days
        )
    capacities = compute_capacities(state_flat, ctx)
    result = solve_with_context(
        prune.kept,
        breakdowns,
        capacities,
        ctx,
        batch=batch,
    )
    return result, prune, breakdowns


__all__ = [
    "BalanceContext",
    "Candidate",
    "Cost",
    "HistoryEntry",
    "ORToolsUpgradeOptimizer",
    "PlanStep",
    "PruneResult",
    "ScoreBreakdown",
    "SolverResult",
    "TaskEnvelope",
    "append_entry",
    "apply_command",
    "build_envelope",
    "compute_capacities",
    "enqueue_envelope",
    "envelope_to_redis_payload",
    "generate_candidates",
    "generate_reasons",
    "load_balance_context",
    "load_history",
    "now_ts",
    "plan_top_k",
    "prune_candidates",
    "queue_key",
    "rank_candidates",
    "rejection_reason",
    "scenario_name_for",
    "score_candidate",
    "solve_optimal",
    "solve_with_context",
    "to_int_score",
]
