"""Turn a coordinator MARCH decision into queued scenarios — the IO boundary.

Everything else in this package is pure (decide what *should* run). This module
is the thin async side-effect: given the :class:`CoordinatorDecision` from
:func:`march.plan_march`, it pushes one queue task per committed MARCH slot so the
worker actually runs it.

Mirrors ``stamina.adapter.enqueue_decision`` (the proven consumer-scenario push):
``task_type`` / ``dsl_scenario`` are the scenario key, ``skip_if_duplicate`` keeps
a single run of each scenario in flight, and the cross-domain priority is lifted
into the queue's absolute band (ordinary tasks sit at 80_000) so the winner isn't
buried while preserving the relative order the coordinator chose.

Overlap note: ``stamina.adapter`` also enqueues ``intel_run`` from the demand
table. That allocator is OFF (``budget.yaml enabled: false``), so there's no live
double-dispatch today; the coordinator is the intended owner of MARCH dispatch.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model import MARCH

if TYPE_CHECKING:
    from collections.abc import Mapping

    from scheduler.queue import RedisQueue

    from .model import CoordinatorDecision

# Ordinary queue tasks sit at this absolute priority (see
# ``stamina.adapter.DEFAULT_PRIORITY``); a MARCH winner is lifted to
# ``BASE + cross-domain priority`` so it ranks above background work while the
# relative order between MARCH domains (intel > gather) is preserved.
DISPATCH_PRIORITY_BASE = 80_000


@dataclass(frozen=True, slots=True)
class MarchScenario:
    """The scenario a committed MARCH domain dispatches to."""

    task_type: str
    dsl_scenario: str


# Domain → scenario. Only domains with a real, runnable scenario appear here;
# others (e.g. ``gather`` until the gathering module is enabled) are reported as
# skipped rather than silently dropped.
MARCH_SCENARIOS: dict[str, MarchScenario] = {
    "intel": MarchScenario(task_type="intel_run", dsl_scenario="intel_run"),
}


@dataclass(frozen=True, slots=True)
class MarchEnqueue:
    domain: str
    task_type: str
    channel_id: str
    priority: int


@dataclass(frozen=True, slots=True)
class MarchSkip:
    domain: str
    key: str
    reason: str          # no_scenario | duplicate


@dataclass(frozen=True, slots=True)
class MarchDispatch:
    """What this pass actually queued, and what it didn't (for the trace)."""

    enqueued: tuple[MarchEnqueue, ...]
    skipped: tuple[MarchSkip, ...]


async def dispatch_march(
    decision: CoordinatorDecision,
    *,
    queue: RedisQueue,
    instance_id: str,
    player_id: str,
    now: float,
    scenarios: Mapping[str, MarchScenario] = MARCH_SCENARIOS,
) -> MarchDispatch:
    """Queue one scenario per committed MARCH slot.

    Each MARCH commit's domain is mapped to its scenario; domains without one are
    skipped (``no_scenario``). ``skip_if_duplicate`` means a domain already in
    flight is skipped (``duplicate``) and re-queued on a later tick — so a
    multi-slot commit collapses to one run-per-domain-in-flight, which matches the
    worker's serial per-instance execution.
    """
    enqueued: list[MarchEnqueue] = []
    skipped: list[MarchSkip] = []
    for commit in decision.committed_for(MARCH):
        action = commit.action
        spec = scenarios.get(action.domain)
        if spec is None:
            skipped.append(MarchSkip(action.domain, action.key, "no_scenario"))
            continue
        priority = DISPATCH_PRIORITY_BASE + int(action.priority)
        ok = await queue.schedule(
            task_id=f"march:{action.domain}:{action.key}:{int(now)}",
            player_id=player_id,
            task_type=spec.task_type,
            priority=priority,
            run_at=now,
            instance_id=instance_id,
            dsl_scenario=spec.dsl_scenario,
            args={"march_domain": action.domain, "march_channel": commit.channel_id},
            skip_if_duplicate=True,
            dedup_ignore_region=True,
        )
        if ok:
            enqueued.append(MarchEnqueue(action.domain, spec.task_type, commit.channel_id, priority))
        else:
            skipped.append(MarchSkip(action.domain, action.key, "duplicate"))
    return MarchDispatch(enqueued=tuple(enqueued), skipped=tuple(skipped))
