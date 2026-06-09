from __future__ import annotations

from typing import TYPE_CHECKING

from dsl import evaluator as evaluator_mod
from dsl.evaluator import ScenarioEvaluator
from dsl.models import Scenario

if TYPE_CHECKING:
    import pytest


def _scenario(task: str) -> Scenario:
    return Scenario.model_validate(
        {
            "name": "Regular",
            "enabled": True,
            "steps": [{"task": task, "cooldown": "1m"}],
        }
    )


def test_unknown_task_type_is_recorded_and_deduped() -> None:
    ev = ScenarioEvaluator()
    state: dict[str, object] = {"player_id": "p1"}

    assert ev.expand_to_tasks(_scenario("no_such_task"), state) == []
    # Second player, same broken step — must not duplicate the entry.
    state2: dict[str, object] = {"player_id": "p2"}
    assert ev.expand_to_tasks(_scenario("no_such_task"), state2) == []

    failures = ev.drain_expand_failures()
    assert len(failures) == 1
    assert failures[0]["scenario"] == "Regular"
    assert failures[0]["task"] == "no_such_task"
    assert "unknown task type" in str(failures[0]["error"])

    # Drained — next drain is empty until the failure recurs.
    assert ev.drain_expand_failures() == []


def test_factory_exception_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ExplodingTask:
        def __init__(self, **kwargs: object) -> None:
            msg = "bad kwargs"
            raise ValueError(msg)

    monkeypatch.setitem(evaluator_mod._TASK_FACTORIES, "exploding", _ExplodingTask)
    ev = ScenarioEvaluator()
    state: dict[str, object] = {"player_id": "p1"}

    assert ev.expand_to_tasks(_scenario("exploding"), state) == []

    failures = ev.drain_expand_failures()
    assert len(failures) == 1
    assert failures[0]["task"] == "exploding"
    assert "ValueError: bad kwargs" in str(failures[0]["error"])
