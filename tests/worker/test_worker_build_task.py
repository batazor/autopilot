"""``InstanceWorker._build_task`` must route by ``QueueItem.dsl_scenario``
when it's set, not by ``task_type`` alone.

The optimizer (`optimizer.dispatcher.build_envelope`) stamps
``task_type="dsl_scenario"`` as a generic marker and carries the real
scenario key in the ``dsl_scenario`` field of the queue payload. Falling
back to ``scenario_key=item.task_type`` made the worker try to load
``scenarios/**/dsl_scenario.yaml`` for every optimizer-queued task and
fail with ``scenario_not_found`` — the "Queue for bot" button effectively
did nothing.

These tests pin the routing so that regression can't sneak back in.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import worker.instance_worker as instance_worker
from scheduler.queue import QueueItem
from tasks.dsl_scenario import DslScenarioTask


def _make_worker() -> instance_worker.InstanceWorker:
    """Bare worker instance — ``_build_task`` only reads ``self._redis``."""
    worker = object.__new__(instance_worker.InstanceWorker)
    worker._cfg = SimpleNamespace(instance_id="bs1")  # type: ignore[attr-defined]
    worker._redis = None
    return worker


def _qitem(**overrides: object) -> QueueItem:
    defaults: dict[str, Any] = {
        "task_id": "t-x",
        "player_id": "p1",
        "task_type": "dsl_scenario",
        "priority": 50_000,
        "run_at": 0.0,
        "instance_id": "bs1",
    }
    defaults.update(overrides)
    return QueueItem(**defaults)  # type: ignore[arg-type]


def test_build_task_prefers_dsl_scenario_field_over_task_type() -> None:
    """Optimizer envelope shape: ``task_type='dsl_scenario'`` + real key in
    the dedicated field. The worker must use the field as the scenario key."""
    worker = _make_worker()
    item = _qitem(task_type="dsl_scenario", dsl_scenario="level_up_bahiti")
    task = instance_worker.InstanceWorker._build_task(worker, item)
    assert isinstance(task, DslScenarioTask)
    assert task.scenario_key == "level_up_bahiti"


def test_build_task_falls_back_to_task_type_when_no_dsl_scenario_field() -> None:
    """Overlay / cron / debug paths put the scenario key directly in
    ``task_type`` and don't set ``dsl_scenario``. Fallback must still work."""
    worker = _make_worker()
    item = _qitem(task_type="who_i_am", dsl_scenario=None)
    task = instance_worker.InstanceWorker._build_task(worker, item)
    assert isinstance(task, DslScenarioTask)
    assert task.scenario_key == "who_i_am"


def test_build_task_treats_blank_dsl_scenario_as_fallback() -> None:
    """Defensive: a queue payload that carries an empty ``dsl_scenario``
    field (whitespace only) should still fall back to ``task_type``."""
    worker = _make_worker()
    item = _qitem(task_type="who_i_am", dsl_scenario="   ")
    task = instance_worker.InstanceWorker._build_task(worker, item)
    assert isinstance(task, DslScenarioTask)
    assert task.scenario_key == "who_i_am"


def test_build_task_threads_region_and_steps_index() -> None:
    """Other ``QueueItem`` fields the DSL scenario depends on still flow
    through — region for tap context, ``start_step_index`` for resume."""
    worker = _make_worker()
    item = _qitem(
        task_type="dsl_scenario",
        dsl_scenario="claim_trials",
        region="popup.claim",
        args={"region": "deals.tabs_strip"},
        start_step_index=3,
        effective_priority=88_000,
    )
    task = instance_worker.InstanceWorker._build_task(worker, item)
    assert isinstance(task, DslScenarioTask)
    assert task.scenario_key == "claim_trials"
    assert task.tap_region == "popup.claim"
    assert task.args == {"region": "deals.tabs_strip"}
    assert task.start_step_index == 3
    assert task.effective_priority == 88_000
