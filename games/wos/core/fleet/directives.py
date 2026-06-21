"""Map a planner :class:`StepDirective` to a concrete coord bus directive.

Wired kinds (``run_scenario``) become an ``enqueue_scenario`` directive targeting
the participant's instance; deferred kinds (switch/recall/attack/reinforce) return
``None`` with a ``deferred`` status so the dispatcher records them without firing.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from coord.models import Directive, DirectiveTarget

from . import step_kinds as sk

if TYPE_CHECKING:
    from coord.campaign import StepDirective

# Dispatch status labels (mirror MarchDispatch.enqueued/skipped traces).
POSTED = "posted"
DEFERRED = "deferred"
NO_SCENARIO = "no_scenario"


def to_coord_directive(
    sd: StepDirective, *, source: str = "fleet_orchestrator"
) -> tuple[Directive | None, str]:
    """Return ``(directive_or_None, status)``.

    * ``run_scenario`` with a scenario → an ``enqueue_scenario`` directive
      (``posted``), targeting the resolved instance.
    * ``run_scenario`` without a scenario → ``no_scenario`` (skip).
    * everything else → ``deferred`` (no on-device scenario yet).
    """
    if sd.kind != sk.RUN_SCENARIO:
        return (None, DEFERRED)
    if not sd.scenario:
        return (None, NO_SCENARIO)

    payload: dict[str, object] = {"scenario": sd.scenario, "player_id": sd.fid}
    if sd.params:
        payload["args"] = dict(sd.params)
    # Carry shared-device sequencing hints for the future switch wiring.
    if sd.sequence_group:
        payload["sequence_group"] = sd.sequence_group
        payload["sequence_order"] = sd.sequence_order

    directive = Directive(
        directive_id=sd.idempotency_key,
        kind="enqueue_scenario",
        target=DirectiveTarget.instance(sd.instance_id),
        payload=payload,
        source=source,
        idempotency_key=sd.idempotency_key,
    )
    return (directive, POSTED)
