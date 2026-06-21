"""Pure campaign planner — one tick for one run, no IO. Mirrors ``plan_march``.

Decides, deterministically (``now`` injected), what to do for an active campaign
run this tick: which directives to post, whether to advance the phase, or to
abort + roll back. The WoS adapter populates the ``FleetSnapshot`` (signal flags
read off device state) and ``CalendarView`` before calling this, and dispatches
the returned :class:`CampaignDecision` over the coord bus.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from . import barrier as _barrier
from .barrier import PH_READY, PH_TIMED_OUT
from .model import (
    ABORTED,
    ADVANCE,
    DONE,
    HOLD,
    RUNNING,
    TRIGGER_CALENDAR,
    CampaignDecision,
    StepDirective,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .model import (
        CampaignDef,
        CampaignRun,
        Participant,
        ParticipantStatus,
        Phase,
        Step,
    )
    from .protocols import CalendarView, FleetSnapshot


def _select(selector: str, participants: Sequence[Participant]) -> list[Participant]:
    if selector in ("all", "any", ""):
        return list(participants)
    return [p for p in participants if p.role == selector or p.fid == selector]


def _acting(phase: Phase, participants: Sequence[Participant]) -> list[Participant]:
    """Union of the participants targeted by any of the phase's steps (order-stable)."""
    seen: dict[str, Participant] = {}
    for step in phase.steps:
        for p in _select(step.role_selector, participants):
            seen.setdefault(p.fid, p)
    return list(seen.values())


def _idem(run: CampaignRun, phase_index: int, fid: str, kind: str) -> str:
    return f"{run.run_id}:{phase_index}:{fid}:{kind}"


def _set_reached(
    statuses: Sequence[ParticipantStatus], reached: set[str]
) -> tuple[ParticipantStatus, ...]:
    return tuple(
        replace(s, reached=True) if (s.fid in reached and not s.reached) else s
        for s in statuses
    )


def _directive_for(
    run: CampaignRun,
    phase_index: int,
    participant: Participant,
    step: Step,
    *,
    suffix: str = "",
    sequence_order: int = 0,
) -> StepDirective:
    base = _idem(run, phase_index, participant.fid, step.kind)
    key = f"{base}:{suffix}" if suffix else base
    group = participant.instance_id if step.requires_switch else ""
    return StepDirective(
        fid=participant.fid,
        instance_id=participant.instance_id,
        kind=step.kind,
        scenario=step.scenario,
        params=dict(step.params),
        idempotency_key=key,
        requires_switch=step.requires_switch,
        sequence_group=group,
        sequence_order=sequence_order,
    )


def _advance(
    cdef: CampaignDef,
    run: CampaignRun,
    statuses: tuple[ParticipantStatus, ...],
    trace: list[str],
) -> CampaignDecision:
    nxt = run.phase_index + 1
    if nxt >= len(cdef.phases):
        trace.append("campaign_complete")
        return CampaignDecision(next_status=DONE, updated_statuses=statuses, trace=tuple(trace))
    trace.append(f"advance_to_phase_{nxt}")
    reset = tuple(
        replace(s, reached=False, last_directive_id="", failed=False) for s in statuses
    )
    return CampaignDecision(
        advance_to=nxt, next_status=RUNNING, updated_statuses=reset, trace=tuple(trace)
    )


def _abort(
    cdef: CampaignDef,
    run: CampaignRun,
    statuses: tuple[ParticipantStatus, ...],
    trace: list[str],
) -> CampaignDecision:
    """Abort the run, emitting the current phase's rollback steps (e.g. farm
    "resume troops"). The safety invariant — farm resumes if the fighter never
    attacked — is structural: the fighter's phase is gated behind ``city_empty``
    and the farm-recall phase's rollback resumes troops."""
    directives: list[StepDirective] = []
    if 0 <= run.phase_index < len(cdef.phases):
        phase = cdef.phases[run.phase_index]
        directives = [
            _directive_for(run, run.phase_index, p, step, suffix="rollback")
            for step in phase.rollback
            for p in _select(step.role_selector, run.participants)
        ]
    trace.append("aborted")
    return CampaignDecision(
        directives=tuple(directives),
        next_status=ABORTED,
        updated_statuses=statuses,
        trace=tuple(trace),
    )


def _emit_active_phase(
    run: CampaignRun,
    phase: Phase,
    statuses: tuple[ParticipantStatus, ...],
    device_order: Mapping[str, int] | None = None,
) -> tuple[list[StepDirective], tuple[ParticipantStatus, ...]]:
    """Emit one directive per (step, selected participant) not already in flight.

    Shared-device steps (``requires_switch``) are grouped by instance_id with an
    ascending ``sequence_order`` so the dispatcher serializes switch→act→switch.
    ``device_order`` (from the device scheduler) overrides that order so the
    highest-value account on a shared device is serviced first.
    """
    by_fid = {s.fid: s for s in statuses}
    directives: list[StepDirective] = []
    order_counter: dict[str, int] = {}
    for step in phase.steps:
        for p in _select(step.role_selector, run.participants):
            key = _idem(run, run.phase_index, p.fid, step.kind)
            s = by_fid.get(p.fid)
            if s is not None and s.last_directive_id == key and not s.failed:
                continue  # already in flight this phase
            order = 0
            if step.requires_switch:
                if device_order is not None and p.fid in device_order:
                    order = device_order[p.fid]
                else:
                    order = order_counter.get(p.instance_id, 0)
                    order_counter[p.instance_id] = order + 1
            directives.append(
                _directive_for(run, run.phase_index, p, step, sequence_order=order)
            )
            if s is not None:
                by_fid[p.fid] = replace(s, last_directive_id=key, failed=False)
    return directives, tuple(by_fid.values())


def plan_campaign_tick(
    cdef: CampaignDef,
    run: CampaignRun,
    fleet: FleetSnapshot,
    calendar: CalendarView,
    now: float,
    *,
    device_order: Mapping[str, int] | None = None,
) -> CampaignDecision:
    if run.status in (DONE, ABORTED):
        return CampaignDecision(
            next_status=run.status, updated_statuses=run.statuses, trace=("terminal",)
        )

    trace: list[str] = []

    # Whole-run deadline backstop.
    if now >= run.deadline_at:
        trace.append("run_deadline_exceeded")
        return _abort(cdef, run, run.statuses, trace)

    # Calendar gate (calendar-anchored campaigns only).
    if (
        cdef.trigger == TRIGGER_CALENDAR
        and cdef.anchor_event_slug
        and not calendar.window_active(cdef.anchor_event_slug)
    ):
        if run.status == RUNNING and run.phase_index > 0:
            trace.append("calendar_window_closed_mid_run")
            return _abort(cdef, run, run.statuses, trace)
        trace.append("calendar_window_inactive")
        return CampaignDecision(
            next_status=run.status, updated_statuses=run.statuses, trace=tuple(trace)
        )

    phase = cdef.phases[run.phase_index]
    acting = _acting(phase, run.participants)
    acting_fids = {p.fid for p in acting}

    # Per-participant barrier signals (sticky within a phase).
    prior = {s.fid for s in run.statuses if s.reached}
    reached_now: set[str] = set()
    if phase.barrier.signal:
        reached_now = {p.fid for p in acting if fleet.signal(p.fid, phase.barrier.signal)}
    effective = (prior | reached_now) & acting_fids
    statuses = _set_reached(run.statuses, effective)

    outcome = _barrier.phase_outcome(
        phase.barrier, acting_fids, effective, run.phase_started_at, now
    )

    if outcome == PH_READY:
        return _advance(cdef, run, statuses, trace)

    if outcome == PH_TIMED_OUT:
        policy = phase.barrier.on_timeout
        if policy == ADVANCE:
            trace.append("timed_out_advance")
            return _advance(cdef, run, statuses, trace)
        if policy == HOLD:
            trace.append("timed_out_hold")
            return CampaignDecision(
                next_status=RUNNING, updated_statuses=statuses, trace=tuple(trace)
            )
        trace.append("timed_out_abort")
        return _abort(cdef, run, statuses, trace)

    # WAITING — hold if a required participant is offline (don't advance, don't spam).
    offline = sorted(p.fid for p in acting if not fleet.online(p.fid))
    if offline:
        trace.append("hold_offline:" + ",".join(offline))
        return CampaignDecision(
            next_status=RUNNING, updated_statuses=statuses, trace=tuple(trace)
        )

    directives, statuses2 = _emit_active_phase(run, phase, statuses, device_order)
    trace.append(f"emit:{len(directives)}" if directives else "waiting")
    return CampaignDecision(
        directives=tuple(directives),
        next_status=RUNNING,
        updated_statuses=statuses2,
        trace=tuple(trace),
    )
