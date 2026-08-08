"""Top-level step dispatch: no silent holes, and nested failures keep their trace.

Two long-standing gaps in the split between the top-level and nested step
interpreters:

* the top-level dispatch was an if-chain with no ``else``, so an unrecognised
  step key ran as a no-op with no log and no trace row;
* the nested interpreter had no access to the ``ExecFrame``, so a failure inside
  a ``loop`` / ``while_match`` body returned metadata with no ``steps_trace`` —
  and ``botctl trace`` then fell back to an older history row while still
  presenting it as this run's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for

if TYPE_CHECKING:
    from pathlib import Path


def _write_repo(tmp_path: Path, steps: list[dict[str, Any]]) -> None:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "dispatch_probe.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "device_level": True,
                "name": "Dispatch probe",
                "steps": steps,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")


def _task(redis_async: object) -> Any:
    return dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="dispatch_probe",
        redis_client=redis_async,  # type: ignore[arg-type]
    )


@pytest.fixture
def _blank_actions(mocker: Any, tmp_path: Path) -> Any:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    actions = make_actions([frame], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    return actions


def _rows(result: Any) -> list[dict[str, Any]]:
    trace = result.metadata.get("steps_trace")
    assert isinstance(trace, list), result.metadata
    return trace


@pytest.mark.asyncio
async def test_unknown_step_key_is_traced_not_swallowed(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    """A typo'd step used to vanish: no branch matched and the chain fell through."""
    _write_repo(tmp_path, [{"waaait": "1s"}, {"wait": "1ms"}])

    result = await _task(redis_async).execute("bs1")

    assert result.success is True
    reasons = [r.get("reason") for r in _rows(result)]
    assert "unknown_step_key" in reasons


@pytest.mark.asyncio
async def test_break_outside_a_loop_is_traced(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    """``break`` is a nested-only construct; at top level it had no branch at all."""
    _write_repo(tmp_path, [{"break": "loop"}, {"wait": "1ms"}])

    result = await _task(redis_async).execute("bs1")

    assert result.success is True
    reasons = [r.get("reason") for r in _rows(result)]
    assert "break_outside_loop" in reasons


@pytest.mark.asyncio
async def test_nested_failure_carries_the_step_trace(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    """A failure raised from inside a loop body must still report steps_trace.

    Without it the history row has no trace and ``botctl trace`` silently serves
    an older run's rows under this run's name.
    """
    _write_repo(
        tmp_path,
        [{"wait": "1ms"}, {"loop": {"max": 2, "steps": [{"exec": "boom"}]}}],
    )

    async def _boom(ctx: Any) -> None:
        ctx.fail("nested_gave_up")

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"boom": _boom}, clear=False)

    result = await _task(redis_async).execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "nested_gave_up"
    rows = _rows(result)
    assert rows, "nested failure returned an empty trace"
    # The step before the loop must be in there — proof this is the run's own
    # accumulated trace and not a stub built at the failure site.
    assert any("wait" in str(r.get("step", r)) for r in rows), rows
    assert result.metadata["scenario_completed"] is False
