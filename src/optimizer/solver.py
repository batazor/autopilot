"""CP-SAT backend over the candidate pool.

Mirrors the skeleton in ``whiteout_survival_gen_1_hero_progression_plan_yaml.md``:
binary ``x_command``, integer ``Σ score * x`` objective, linear capacity
constraints, optional dependency implications and mutex groups.

CP-SAT requires integer coefficients (see the OR-Tools docs); the
existing :mod:`optimizer.scorer` returns floats so we scale by
``_SCORE_SCALE`` and round before handing to the model.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from optimizer.context import BalanceContext
from optimizer.scorer import ScoreBreakdown
from optimizer.types import Candidate

_SCORE_SCALE = 100
"""Multiply float ``final_score`` by this before rounding to int — keeps
two decimals' worth of ranking granularity inside CP-SAT integer math."""


@dataclass(frozen=True)
class SolverResult:
    selected: list[Candidate]
    objective_value: int
    status: str
    """``OPTIMAL`` / ``FEASIBLE`` / ``INFEASIBLE`` / ``MODEL_INVALID`` /
    ``UNKNOWN`` — comes straight from ``CpSolver.StatusName``."""
    chosen_ids: tuple[str, ...] = field(default_factory=tuple)


def to_int_score(score: float) -> int:
    """Scale & round float score to a non-negative integer (CP-SAT input)."""
    return max(0, int(round(score * _SCORE_SCALE)))


def solver_params(ctx: BalanceContext, *, batch: bool = False) -> dict[str, float | int]:
    """Pull ``defaults.solver.online`` / ``.batch`` into a flat dict the
    caller can pass through to :func:`select`. Falls back to OR-Tools-
    sensible defaults when fields are missing."""
    sub = ((ctx.defaults.get("solver") or {}).get("batch" if batch else "online") or {})
    return {
        "time_limit_seconds": float(sub.get("max_time_in_seconds", 0.25)),
        "workers": int(sub.get("num_search_workers", 4)),
        "random_seed": int((ctx.defaults.get("solver") or {}).get("random_seed", 42)),
    }


class ORToolsUpgradeOptimizer:
    """Stateless wrapper around CP-SAT. One instance per call site is fine."""

    def __init__(
        self,
        *,
        time_limit_seconds: float = 0.25,
        workers: int = 4,
        random_seed: int = 42,
    ) -> None:
        self.time_limit_seconds = float(time_limit_seconds)
        self.workers = int(workers)
        self.random_seed = int(random_seed)

    def select(
        self,
        candidates: list[Candidate],
        scores: dict[str, int],
        capacities: dict[str, int],
        *,
        implications: Iterable[tuple[str, str]] = (),
        mutex_groups: dict[str, list[str]] | None = None,
    ) -> SolverResult:
        """Build & solve the model. ``scores`` is keyed by ``Candidate.id``.

        ``implications`` is an iterable of ``(child_id, parent_id)``: the
        child binary can only be 1 when the parent is also 1 (chain
        prefix). ``mutex_groups`` is a ``{group_name: [candidate_id…]}``
        map; at most one candidate per group is selected.
        """
        model = cp_model.CpModel()
        x = {c.id: model.NewBoolVar(c.id) for c in candidates}  # ty: ignore[unresolved-attribute]

        # Resource capacities: Σ cost[r] * x ≤ capacity[r] for every
        # resource any candidate touches. Resources missing from
        # ``capacities`` default to 0 — candidates that need them get
        # forced to x=0, which is what we want when state has no
        # inventory tracked (safer to skip than to over-spend).
        all_resources: set[str] = set(capacities)
        for c in candidates:
            for cost in c.costs:
                all_resources.add(cost.resource)
        for resource in all_resources:
            cap = int(max(0, capacities.get(resource, 0)))
            pairs = [
                (int(cost_for(c, resource)), x[c.id])
                for c in candidates
            ]
            pairs = [(amount, var) for amount, var in pairs if amount > 0]
            if not pairs:
                continue
            model.Add(sum(amount * var for amount, var in pairs) <= cap)  # ty: ignore[unresolved-attribute]

        for child_id, parent_id in implications:
            if child_id in x and parent_id in x:
                model.Add(x[child_id] <= x[parent_id])  # ty: ignore[unresolved-attribute]

        for ids in (mutex_groups or {}).values():
            in_model = [cid for cid in ids if cid in x]
            if in_model:
                model.Add(sum(x[cid] for cid in in_model) <= 1)  # ty: ignore[unresolved-attribute]

        model.Maximize(sum(int(scores.get(c.id, 0)) * x[c.id] for c in candidates))  # ty: ignore[unresolved-attribute]

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_seconds
        solver.parameters.num_search_workers = self.workers
        solver.parameters.random_seed = self.random_seed

        status_code = solver.Solve(model)
        status = solver.StatusName(status_code)

        selected: list[Candidate] = []
        if status_code in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            selected = [c for c in candidates if solver.BooleanValue(x[c.id])]
            selected.sort(key=lambda c: scores.get(c.id, 0), reverse=True)

        return SolverResult(
            selected=selected,
            objective_value=int(solver.ObjectiveValue()) if selected else 0,
            status=status,
            chosen_ids=tuple(c.id for c in selected),
        )


def cost_for(c: Candidate, resource: str) -> int:
    """Sum of all ``Cost(resource=…)`` amounts on the candidate matching
    ``resource``. Multiple Costs for the same resource (rare today) add
    up; foreign resources contribute 0."""
    total = 0
    for cost in c.costs:
        if cost.resource == resource:
            total += int(cost.amount)
    return total


def solve_with_context(
    candidates: list[Candidate],
    breakdowns: dict[str, ScoreBreakdown],
    capacities: dict[str, int],
    ctx: BalanceContext,
    *,
    implications: Iterable[tuple[str, str]] = (),
    mutex_groups: dict[str, list[str]] | None = None,
    batch: bool = False,
) -> SolverResult:
    """Convenience wrapper: builds int-score map + reads solver params
    from balance defaults, then delegates to
    :meth:`ORToolsUpgradeOptimizer.select`."""
    scores = {cid: to_int_score(br.final_score) for cid, br in breakdowns.items()}
    params = solver_params(ctx, batch=batch)
    optimizer = ORToolsUpgradeOptimizer(
        time_limit_seconds=float(params["time_limit_seconds"]),
        workers=int(params["workers"]),
        random_seed=int(params["random_seed"]),
    )
    return optimizer.select(
        candidates,
        scores,
        capacities,
        implications=implications,
        mutex_groups=mutex_groups,
    )
