"""Single-device scheduling with switch cost — order accounts on a shared device.

When several accounts live on one device they're serviced serially (one active
account at a time) and each switch costs time. With a window deadline, we can't
always service them all — so pick the highest-value subset and the order that
keeps each serviced account on-time.

Folding the uniform switch cost into each job's processing time
(``p_j = switch + service``) makes this exactly ``1||ΣwⱼUⱼ`` (maximise weighted
on-time jobs on one machine). The optimum schedules the chosen jobs in
**earliest-deadline-first** order, so an EDD sort + a DP over (index, elapsed)
solves it exactly. Pure, ``now``-free.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True, slots=True)
class Job:
    account_id: str
    service_s: float      # time to run this account's action
    value: float          # worth of servicing it (event points / priority)
    deadline_s: float     # must COMPLETE by this (window close / phase deadline)


@dataclass(frozen=True, slots=True)
class DeviceSchedule:
    order: tuple[str, ...]      # serviced accounts, in service (EDD) order
    dropped: tuple[str, ...]    # accounts that can't be serviced on-time
    total_value: float
    makespan: float            # when the last serviced account finishes


def schedule_device(
    jobs: Sequence[Job], *, switch_s: float = 0.0, start_time: float = 0.0
) -> DeviceSchedule:
    """Optimal max-value on-time schedule for one device. ``switch_s`` is the
    per-account switch cost (added to every job, including the first)."""
    if not jobs:
        return DeviceSchedule(order=(), dropped=(), total_value=0.0, makespan=start_time)

    # EDD order is optimal for the on-time set; break ties by higher value.
    ordered = sorted(jobs, key=lambda j: (j.deadline_s, -j.value, j.account_id))
    n = len(ordered)
    memo: dict[tuple[int, float], tuple[float, tuple[int, ...]]] = {}

    def solve(i: int, elapsed: float) -> tuple[float, tuple[int, ...]]:
        if i == n:
            return (0.0, ())
        key = (i, elapsed)
        cached = memo.get(key)
        if cached is not None:
            return cached
        best = solve(i + 1, elapsed)                       # skip job i
        finish = elapsed + switch_s + ordered[i].service_s
        if finish <= ordered[i].deadline_s:                # take it if on-time
            sub_value, sub_chosen = solve(i + 1, finish)
            taken_value = ordered[i].value + sub_value
            if taken_value > best[0]:
                best = (taken_value, (i, *sub_chosen))
        memo[key] = best
        return best

    total_value, chosen = solve(0, start_time)
    chosen_set = set(chosen)
    order = tuple(ordered[i].account_id for i in chosen)
    dropped = tuple(ordered[i].account_id for i in range(n) if i not in chosen_set)
    makespan = start_time
    for i in chosen:
        makespan += switch_s + ordered[i].service_s
    return DeviceSchedule(
        order=order, dropped=dropped, total_value=total_value, makespan=makespan
    )
