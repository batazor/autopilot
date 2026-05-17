"""Apply a chosen candidate to a state snapshot (dry-run / planner).

Stays purely functional: ``apply_command`` returns a *new* flat-state
dict, never mutates the caller's. This lets the planner step through
``solve → execute top-1 → re-solve`` cycles in memory without touching
the real :class:`GamerStateStore`.

When the bot eventually wires actual mutation (write back via the state
store, fire executor tasks), it should call the same ``apply_command``
plus a side-effect — keeping the planner and the executor in sync on
state semantics.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from optimizer import generate_candidates, prune_candidates, score_candidate
from optimizer.capacities import _GLOBAL_RESOURCE_KEYS, compute_capacities
from optimizer.context import BalanceContext, load_balance_context
from optimizer.solver import solve_with_context

if TYPE_CHECKING:
    from optimizer.scorer import ScoreBreakdown
    from optimizer.types import Candidate


@dataclass(frozen=True)
class PlanStep:
    candidate: Candidate
    breakdown: ScoreBreakdown
    state_before: dict[str, object]
    state_after: dict[str, object]
    capacities_before: dict[str, int]
    capacities_after: dict[str, int]


def apply_command(
    state_flat: dict[str, object], candidate: Candidate
) -> dict[str, object]:
    """Return a copy of ``state_flat`` with ``candidate`` applied.

    Mutations:

    * ``level_up``        → ``heroes.entries.<hid>.level`` ← ``to_level``
    * ``star_tier_up``    → ``heroes.entries.<hid>.star_progress`` ← ``to_progress``
    * ``skill_up``        → ``heroes.entries.<hid>.skills.<track>.<slot>`` ← ``to_level``

    Plus a best-effort resource deduction: subtracts ``cost.amount``
    from the matching state key (global resource or per-hero shard
    bucket). Unknown resources are left alone — the solver had already
    rejected them upstream when the budget was 0.
    """
    new: dict[str, object] = dict(state_flat)
    payload = candidate.payload or {}
    hid = candidate.hero_id or ""

    if candidate.action == "level_up":
        to_lv = payload.get("to_level")
        if isinstance(to_lv, int):
            new[f"heroes.entries.{hid}.level"] = to_lv
    elif candidate.action == "star_tier_up":
        to_prog = payload.get("to_progress")
        if isinstance(to_prog, int):
            new[f"heroes.entries.{hid}.star_progress"] = to_prog
    elif candidate.action == "skill_up":
        track = payload.get("track")
        slot = payload.get("slot")
        to_lv = payload.get("to_level")
        if track and slot is not None and isinstance(to_lv, int):
            new[f"heroes.entries.{hid}.skills.{track}.{slot}"] = to_lv

    for cost in candidate.costs:
        _deduct_resource(new, cost.resource, int(cost.amount), hero_id=hid)
    return new


def _deduct_resource(
    state: dict[str, object], resource: str, amount: int, *, hero_id: str = ""
) -> None:
    """In-place subtraction on the matching state key. Tries the global
    resource map first; falls back to per-hero shard fields for
    ``{hero_id}_shard`` resources."""
    if amount <= 0:
        return
    for key in _GLOBAL_RESOURCE_KEYS.get(resource, ()):
        if key in state:
            raw = state[key]
            try:
                cur = int(raw) if isinstance(raw, (int, float, str, bytes, bytearray)) else None
            except (TypeError, ValueError):
                cur = None
            if cur is None:
                continue
            state[key] = max(0, cur - amount)
            return
    if resource.endswith("_shard") and hero_id:
        key = f"heroes.entries.{hero_id}.shards_current"
        if key in state:
            raw = state[key]
            try:
                cur = int(raw) if isinstance(raw, (int, float, str, bytes, bytearray)) else 0
            except (TypeError, ValueError):
                cur = 0
            state[key] = max(0, cur - amount)


def plan_top_k(
    state_flat: dict[str, object],
    ctx: BalanceContext | None = None,
    *,
    k: int = 5,
    server_age_days: int = 0,
) -> list[PlanStep]:
    """Roll ``solve → apply top 1 → re-solve`` ``k`` times.

    Returns the chosen step at each iteration with full breakdown +
    state diff so callers (UI / executor / replay tests) can render or
    assert on it. Stops early when the solver returns no selection.
    """
    ctx = ctx or load_balance_context()
    plan: list[PlanStep] = []
    state = dict(state_flat)
    for _ in range(max(0, int(k))):
        candidates = generate_candidates(state, ctx)
        prune = prune_candidates(candidates, state, ctx)
        if not prune.kept:
            break
        breakdowns = {
            c.id: score_candidate(c, ctx, state, server_age_days=server_age_days)
            for c in prune.kept
        }
        caps_before = compute_capacities(state, ctx)
        result = solve_with_context(prune.kept, breakdowns, caps_before, ctx)
        if not result.selected:
            break
        top = result.selected[0]
        new_state = apply_command(state, top)
        caps_after = compute_capacities(new_state, ctx)
        plan.append(
            PlanStep(
                candidate=top,
                breakdown=breakdowns[top.id],
                state_before=state,
                state_after=new_state,
                capacities_before=caps_before,
                capacities_after=caps_after,
            )
        )
        state = new_state
    return plan
