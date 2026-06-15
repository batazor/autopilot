"""The coordinator: fill idle channels with the best affordable actions.

Pure greedy allocation. Given the idle execution channels, the candidate actions
every domain proposed (each with a cross-domain ``priority`` and shared ``cost``),
and the current resource balances, it walks candidates highest-priority-first and
commits each to a free channel of its kind **iff** the shared budget still covers
it — decrementing the budget as it goes so two domains can't spend the same wood.

A high-priority action that's blocked only by resources doesn't waste its channel:
it's recorded as ``starved`` (and its short resources noted as the bottleneck) and
the channel goes to the next affordable candidate of that kind. The bottleneck
signal is what lets the economy loop react ("starved on coal → raise gather/coal").
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .model import Commit, CoordinatorDecision

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .model import CandidateAction, Channel


def coordinate(
    channels: Sequence[Channel],
    candidates: Sequence[CandidateAction],
    balances: Mapping[str, int],
) -> CoordinatorDecision:
    """Allocate idle ``channels`` to the best affordable ``candidates``.

    ``balances`` are the shared resource pools (canonical keys). Returns the
    commits (≤1 per channel), the starved/no-channel candidates, the leftover
    balances, and the set of resources that blocked something.
    """
    # Idle channel ids grouped by kind (FIFO so ids stay stable/deterministic).
    free: dict[str, list[str]] = {}
    for ch in channels:
        free.setdefault(ch.kind, []).append(ch.id)

    remaining: dict[str, int] = {k: int(v) for k, v in balances.items()}
    commits: list[Commit] = []
    starved: list[CandidateAction] = []
    no_channel: list[CandidateAction] = []
    bottleneck: set[str] = set()

    ordered = sorted(candidates, key=lambda c: (-c.priority, c.domain, c.key))
    for c in ordered:
        lane = free.get(c.channel_kind)
        if not lane:
            no_channel.append(c)
            continue
        short = [r for r, amt in c.cost.items() if amt > remaining.get(r, 0)]
        if short:
            starved.append(c)              # keep the channel for a cheaper sibling
            bottleneck.update(short)
            continue
        channel_id = lane.pop(0)
        for r, amt in c.cost.items():
            remaining[r] = remaining.get(r, 0) - amt
        commits.append(Commit(channel_id=channel_id, action=c))

    return CoordinatorDecision(
        commits=tuple(commits),
        starved=tuple(starved),
        no_channel=tuple(no_channel),
        remaining=remaining,
        bottleneck_resources=tuple(sorted(bottleneck)),
    )
