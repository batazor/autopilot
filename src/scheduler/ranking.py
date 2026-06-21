"""Pure task-ranking arithmetic (ADR 0001 §"Ranking model").

Extracted from :mod:`scheduler.queue` so the sort-key formula can be reasoned
about and unit-tested without a ``RedisQueue``, Redis, or the screen graph.
:func:`compute_rank` is a pure function: the caller resolves the candidate's
``required_node`` and BFS ``hops`` (so graph topology / monkeypatching stays in
``queue.py``) and this module turns them into the final sort key + metadata.
"""

from __future__ import annotations

from typing import Any

# Dynamic ranking knobs. Defaults are bounded so a single debuff cannot cross a
# configured 10k YAML priority band.
W_HOPS = 500
W_RECENT = 1000
HOPS_DEBUFF_CAP_HOPS = 5
UNREACHABLE_DEBUFF = 5000
HOPS_SENTINEL = 10**9

RankSortKey = tuple[int, int, int, float, float]


def compute_rank(
    *,
    base_priority: int,
    current_screen: str,
    required_node: str,
    hops: int | None,
    recent_count: int,
    recent_runs_cap: int,
    no_recent_debuff_member: bool,
    run_at: float,
    created_at: float,
) -> tuple[RankSortKey, dict[str, Any]]:
    """Score one due candidate.

    ``hops`` is the BFS distance ``current_screen → required_node`` (``None`` =
    unreachable); it is only consulted when both ``required_node`` and
    ``current_screen`` are non-empty — the caller need not compute it otherwise.

    Returns ``(sort_key, meta)`` where ``sort_key`` follows ADR 0001
    §"Final sort key": ``(-effective_priority, unreachable_flag, hops, run_at,
    created_at)``. Callers sort ascending — smallest tuple runs first.
    """
    if not required_node or not current_screen:
        unreachable_flag = 0
        hops_val = 0
        graph_debuff = 0
    elif hops is None:
        unreachable_flag = 1
        hops_val = HOPS_SENTINEL
        graph_debuff = UNREACHABLE_DEBUFF
    else:
        unreachable_flag = 0
        hops_val = hops
        graph_debuff = W_HOPS * min(hops, HOPS_DEBUFF_CAP_HOPS)

    on_required_node = bool(required_node and current_screen == required_node)
    recent_debuff_disabled = no_recent_debuff_member or on_required_node
    recent_debuff = (
        0 if recent_debuff_disabled else min(recent_count, recent_runs_cap) * W_RECENT
    )
    effective_priority = base_priority - graph_debuff - recent_debuff

    sort_key: RankSortKey = (
        -effective_priority,
        unreachable_flag,
        hops_val,
        run_at,
        created_at,
    )
    meta = {
        "base_priority": base_priority,
        "effective_priority": effective_priority,
        "graph_debuff": graph_debuff,
        "recent_debuff": recent_debuff,
        "hops": hops_val,
        "unreachable_flag": unreachable_flag,
        "required_node": required_node,
        "recent_count": recent_count,
        "recent_debuff_disabled": recent_debuff_disabled,
        "on_required_node": on_required_node,
        "current_screen": current_screen,
    }
    return sort_key, meta
