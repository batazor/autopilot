"""Fleet-level arbitration — the brain above the per-run planner.

The per-run planner (``plan_campaign_tick``) decides what one run *wants*. But
runs share contended resources: an account can do one thing at a time, a device
hosts one account at a time. When runs collide on a resource, this layer decides
who acts — a weighted **set-packing**: choose the set of runs, with disjoint
resource claims, that maximises total campaign priority, and report what got
starved (the fleet's contention bottleneck).

Mirrors ``coordinator.coordinate`` one level up: pure, no IO. ``arbitrate`` is a
greedy pass (priority-desc, take-if-free); ``arbitrate_optimal`` is an exact
branch-and-bound that is never worse than greedy and always terminates (falls
back to greedy past a node budget). The WoS layer supplies priorities
(``fleet.objective``) and builds the claims (``account:<fid>`` / ``device:<id>``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    """One run's bid for a bundle of shared resources, on the campaign-priority scale."""

    run_id: str
    priority: float
    resources: frozenset[str]   # e.g. {"account:111", "device:dev-a"}
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ArbitrationResult:
    active: tuple[str, ...]            # run_ids that won their resources this tick
    starved: tuple[str, ...]           # run_ids deferred on contention (HOLD)
    owner: Mapping[str, str]           # resource -> winning run_id
    contended: tuple[str, ...]         # resources that blocked a starved run (bottleneck)


def _result_from_chosen(
    claims: Sequence[ResourceClaim], chosen_ids: set[str]
) -> ArbitrationResult:
    owner: dict[str, str] = {}
    active: list[str] = []
    for c in claims:
        if c.run_id in chosen_ids:
            active.append(c.run_id)
            for r in c.resources:
                owner.setdefault(r, c.run_id)
    starved: list[str] = []
    contended: set[str] = set()
    for c in claims:
        if c.run_id in chosen_ids:
            continue
        starved.append(c.run_id)
        contended.update(r for r in c.resources if r in owner)
    return ArbitrationResult(
        active=tuple(active),
        starved=tuple(starved),
        owner=dict(owner),
        contended=tuple(sorted(contended)),
    )


def arbitrate(claims: Sequence[ResourceClaim]) -> ArbitrationResult:
    """Greedy weighted set-packing: highest priority first, take if its resources
    are all still free. Deterministic (ties break by run_id). Good, not optimal —
    the default, like ``coordinate``."""
    ordered = sorted(claims, key=lambda c: (-c.priority, c.run_id))
    used: set[str] = set()
    chosen: set[str] = set()
    for c in ordered:
        if c.resources & used:
            continue
        chosen.add(c.run_id)
        used |= c.resources
    return _result_from_chosen(claims, chosen)


def arbitrate_optimal(
    claims: Sequence[ResourceClaim], *, max_nodes: int = 200_000
) -> ArbitrationResult:
    """Exact maximum-weight set-packing via branch-and-bound. Never worse than
    :func:`arbitrate`; falls back to it once the node budget is spent (so it
    always terminates on pathological inputs)."""
    items = sorted(claims, key=lambda c: (-c.priority, c.run_id))
    n = len(items)
    if n == 0:
        return _result_from_chosen(claims, set())

    # Suffix sums of priorities give a cheap, valid upper bound for pruning.
    suffix = [0.0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix[i] = suffix[i + 1] + max(0.0, items[i].priority)

    best_value = -1.0
    best_set: set[str] = set()
    nodes = 0
    overflow = False

    def recurse(i: int, used: frozenset[str], value: float, chosen: tuple[str, ...]) -> None:
        nonlocal best_value, best_set, nodes, overflow
        if overflow:
            return
        nodes += 1
        if nodes > max_nodes:
            overflow = True
            return
        if value > best_value:
            best_value = value
            best_set = set(chosen)
        if i >= n or value + suffix[i] <= best_value:
            return
        c = items[i]
        # Branch 1: include c if feasible (resource-disjoint with what's used).
        if not (c.resources & used):
            recurse(i + 1, used | c.resources, value + c.priority, (*chosen, c.run_id))
        # Branch 2: exclude c.
        recurse(i + 1, used, value, chosen)

    recurse(0, frozenset(), 0.0, ())
    if overflow:
        return arbitrate(claims)
    return _result_from_chosen(claims, best_set)
