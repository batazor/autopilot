"""Per-invocation frame shared between ``execute`` and its step handlers.

``DslScenarioExecuteMixin.execute`` builds one :class:`ExecFrame` per scenario
run and hands it to the per-step-kind handler methods on
``DslScenarioStepLoopsMixin`` / ``DslScenarioStepActionsMixin``. The frame
carries the loop-invariant locals (resolved actions, area doc, device size,
scenario key …) plus two closures that must keep living in ``execute`` because
they close over the accumulated ``steps_trace`` list and the live step index:

- ``fin(meta, *, completed)`` — stamps ``steps_trace`` / ``steps_total`` /
  ``scenario_completed`` (+ resume index) onto a TaskResult metadata dict.
- ``mark_step_done()`` — publishes the next step index to the UI progress bar.

``step_index`` is the only mutable field: ``execute`` syncs it to the loop
cursor before dispatching each step, and the ``ocr`` handler advances it when
it consumes a sibling chain of consecutive ``ocr:`` steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


@dataclass
class ExecFrame:
    """Loop-invariant context for one ``execute`` invocation."""

    instance_id: str
    scenario_key: str
    actions: Any
    area_doc: dict[str, Any]
    repo_root: Any
    dev_w: int
    dev_h: int
    steps: list[Any]
    require_identity_resolution: bool
    fin: Callable[..., dict[str, Any]]
    mark_step_done: Callable[[], Awaitable[None]]
    # Loop cursor (index of the NEXT step). Synced from ``execute`` before each
    # dispatch; mutated by the ``ocr`` handler when it batches sibling steps.
    step_index: int = 0
