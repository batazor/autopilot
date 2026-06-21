"""Turn a run's shared-device participants into a device schedule.

When >1 of a run's participants live on one device, they must be serviced one at
a time (switch→act→switch) before the window closes. This builds the scheduling
jobs and runs :func:`coord.campaign.schedule_device` to get the optimal service
order (highest-value first, earliest-deadline, dropping what won't fit). The
result is a ``{fid: rank}`` map the planner uses for each directive's
``sequence_order``.

Per-account service time + value are estimates (real measured scenario durations
+ per-account event contribution are deferred readers) — injectable, with safe
uniform defaults so the ordering is sound today (EDD by the window deadline).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from coord.campaign import Job, schedule_device

if TYPE_CHECKING:
    from collections.abc import Callable

    from coord.campaign import CampaignRun, DeviceSchedule, Participant

# Rough estimates until the readers land (tune later).
DEFAULT_SERVICE_S = 60.0   # time to run one account's action
DEFAULT_SWITCH_S = 20.0    # account-switch cost on a device


def schedules_by_device(
    run: CampaignRun,
    *,
    now: float,
    value_of: Callable[[Participant], float] | None = None,
    service_of: Callable[[Participant], float] | None = None,
    switch_s: float = DEFAULT_SWITCH_S,
) -> dict[str, DeviceSchedule]:
    """Optimal schedule per device that hosts >1 of the run's participants."""
    value_fn = value_of or (lambda _p: 1.0)
    service_fn = service_of or (lambda _p: DEFAULT_SERVICE_S)
    deadline = max(0.0, run.deadline_at - now)   # the window/run closes then

    by_device: dict[str, list[Participant]] = {}
    for p in run.participants:
        by_device.setdefault(p.instance_id, []).append(p)

    out: dict[str, DeviceSchedule] = {}
    for iid, group in by_device.items():
        if len(group) < 2:
            continue   # single-account device needs no sequencing
        jobs = [
            Job(p.fid, service_fn(p), float(value_fn(p)), deadline) for p in group
        ]
        out[iid] = schedule_device(jobs, switch_s=float(switch_s))
    return out


def optimized_device_order(
    run: CampaignRun,
    *,
    now: float,
    value_of: Callable[[Participant], float] | None = None,
    service_of: Callable[[Participant], float] | None = None,
    switch_s: float = DEFAULT_SWITCH_S,
) -> dict[str, int]:
    """``{fid: service_rank}`` for shared-device participants (on-time first,
    dropped last). Accounts on their own device are omitted — the planner falls
    back to emission order (rank 0) for them."""
    order_map: dict[str, int] = {}
    for sched in schedules_by_device(
        run, now=now, value_of=value_of, service_of=service_of, switch_s=switch_s
    ).values():
        # serviced (on-time) first, won't-fit last
        order_map.update(
            {fid: rank for rank, fid in enumerate((*sched.order, *sched.dropped))}
        )
    return order_map
