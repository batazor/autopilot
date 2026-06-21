"""Optimal channel allocation — exact replacement for the greedy coordinator.

:func:`coordinator.coordinate` fills channels greedily (highest priority first,
commit if it fits). Greedy is provably suboptimal when the shared budget binds:
with two construction lanes and 100 wood, one A(value 1000, cost 100) blocks
B(600,50)+C(600,50) — greedy takes A (1000), the optimum is B+C (1200).

This solves the real problem: choose the subset of candidates that **maximises
total value** subject to (a) ≤ one per lane / ≤ cap per channel kind and (b) the
shared per-resource budget — a multi-dimensional multiple-knapsack / generalised
assignment. Exact via branch-and-bound (priority-ordered, capacity-bounded
pruning); a node cap falls back to the greedy result, so it is **never worse than
greedy** and always terminates. Same :class:`CoordinatorDecision` contract, so it
drops into :func:`march.plan_march` / :func:`cycle.plan_cycle` unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .coordinator import coordinate
from .model import Commit, CoordinatorDecision

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .model import CandidateAction, Channel

# Branch-and-bound node budget. Realistic instances (a few candidates per channel,
# an intel board of ~10-15 markers) solve in well under this; beyond it we fall
# back to the greedy result rather than stall.
DEFAULT_MAX_NODES = 1_000_000


def coordinate_optimal(
    channels: Sequence[Channel],
    candidates: Sequence[CandidateAction],
    balances: Mapping[str, int],
    *,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> CoordinatorDecision:
    """Allocate ``channels`` to the value-maximising affordable subset of candidates.

    Returns the same :class:`CoordinatorDecision` as :func:`coordinate` (commits,
    starved, no-channel, leftover balances, bottleneck resources). Falls back to
    the greedy allocation if the search exceeds ``max_nodes``.
    """
    cap: dict[str, int] = {}
    lanes: dict[str, list[str]] = {}
    for ch in channels:
        cap[ch.kind] = cap.get(ch.kind, 0) + 1
        lanes.setdefault(ch.kind, []).append(ch.id)

    # Only candidates with a channel of their kind can be placed; the rest are
    # reported as no_channel (exactly as greedy does).
    items = [c for c in candidates if cap.get(c.channel_kind, 0) > 0]
    no_channel = [c for c in candidates if cap.get(c.channel_kind, 0) == 0]
    # Priority order makes the first DFS solution strong → tighter bound → more pruning.
    items.sort(key=lambda c: (-c.priority, c.domain, c.key))
    n = len(items)
    budget0: dict[str, int] = {r: int(v) for r, v in balances.items()}

    best_value = -1.0
    best_set: tuple[int, ...] = ()
    state = {"nodes": 0, "overflow": False}

    def upper_bound(i: int, value: float, rem_cap: dict[str, int]) -> float:
        # Optimistic completion: add every remaining candidate whose kind still has
        # capacity (ignoring the budget). A valid overestimate → safe to prune on.
        bound = value
        cap_left = dict(rem_cap)
        for j in range(i, n):
            k = items[j].channel_kind
            if cap_left.get(k, 0) > 0:
                bound += items[j].priority
                cap_left[k] -= 1
        return bound

    def dfs(i: int, value: float, rem_cap: dict[str, int],
            rem_budget: dict[str, int], chosen: list[int]) -> None:
        nonlocal best_value, best_set
        if state["overflow"]:
            return
        state["nodes"] += 1
        if state["nodes"] > max_nodes:
            state["overflow"] = True
            return
        if value > best_value:                       # excluding all remaining is a valid solution
            best_value = value
            best_set = tuple(chosen)
        if i >= n or upper_bound(i, value, rem_cap) <= best_value:
            return
        c = items[i]
        k = c.channel_kind
        if rem_cap.get(k, 0) > 0 and all(amt <= rem_budget.get(r, 0) for r, amt in c.cost.items()):
            nb = dict(rem_budget)
            for r, amt in c.cost.items():
                nb[r] -= amt
            rc = dict(rem_cap)
            rc[k] -= 1
            dfs(i + 1, value + c.priority, rc, nb, [*chosen, i])  # include
        dfs(i + 1, value, rem_cap, rem_budget, chosen)             # exclude

    dfs(0, 0.0, dict(cap), budget0, [])

    if state["overflow"]:                            # never worse than greedy
        return coordinate(channels, candidates, balances)

    chosen = set(best_set)
    lane_pool = {k: list(v) for k, v in lanes.items()}
    commits: list[Commit] = []
    spent: dict[str, int] = {}
    for idx in sorted(best_set):                     # priority order (items already sorted)
        c = items[idx]
        commits.append(Commit(channel_id=lane_pool[c.channel_kind].pop(0), action=c))
        for r, amt in c.cost.items():
            spent[r] = spent.get(r, 0) + amt

    remaining = {r: budget0.get(r, 0) - spent.get(r, 0) for r in set(budget0) | set(spent)}
    # Classify the unselected: genuinely resource-blocked → starved (+ bottleneck,
    # the economy-loop signal); crowded off a full set of lanes by higher-value
    # picks → no_channel (it wasn't short on resources). Mirrors greedy's split.
    starved: list[CandidateAction] = []
    crowded: list[CandidateAction] = []
    bottleneck: set[str] = set()
    for i in range(n):
        if i in chosen:
            continue
        c = items[i]
        short = [r for r, amt in c.cost.items() if amt > remaining.get(r, 0)]
        if short:
            starved.append(c)
            bottleneck.update(short)
        else:
            crowded.append(c)

    return CoordinatorDecision(
        commits=tuple(commits),
        starved=tuple(starved),
        no_channel=(*no_channel, *crowded),
        remaining=remaining,
        bottleneck_resources=tuple(sorted(bottleneck)),
    )
