"""``exec:`` steps can fail the scenario.

An ``exec:`` step used to trace ``ok`` whatever the handler reported — a handler
that gave up early, a handler that raised, even a typo'd handler name all looked
like success, and the real problem surfaced later as an unrelated symptom. These
tests pin the failure channel: :meth:`DslExecContext.fail`, dispatcher-level
failures, and the ``optional: true`` opt-out.
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


def _write_exec_repo(tmp_path: Path, steps: list[dict[str, Any]]) -> None:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "exec_probe.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "device_level": True,
                "name": "Exec probe",
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
        scenario_key="exec_probe",
        redis_client=redis_async,  # type: ignore[arg-type]
    )


def _patch_registry(mocker: Any, handlers: dict[str, Any]) -> None:
    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, handlers, clear=False)


@pytest.fixture
def _blank_actions(mocker: Any, tmp_path: Path) -> Any:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    actions = make_actions([frame], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    return actions


@pytest.mark.asyncio
async def test_ctx_fail_ends_the_scenario(
    tmp_path: Path, mocker: Any, redis_async: object
) -> None:
    _write_exec_repo(tmp_path, [{"exec": "probe"}, {"exec": "must_not_run"}])
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    patch_dsl(mocker, make_actions([frame], resolution=(100, 100)), repo_root=tmp_path)
    ran: list[str] = []

    async def _probe(ctx: Any) -> None:
        ran.append("probe")
        ctx.fail("panel_not_opened")

    async def _must_not_run(ctx: Any) -> None:
        ran.append("must_not_run")

    _patch_registry(mocker, {"probe": _probe, "must_not_run": _must_not_run})

    result = await _task(redis_async).execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "panel_not_opened"
    assert result.metadata["exec"] == "probe"
    assert result.metadata["scenario_completed"] is False
    # The step after the failed exec must not run.
    assert ran == ["probe"]


@pytest.mark.asyncio
async def test_handler_that_just_returns_still_succeeds(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    """A handler deciding there is nothing to do has SUCCEEDED — the failure
    channel must stay opt-in, or 400-odd existing handlers change behaviour."""
    _write_exec_repo(tmp_path, [{"exec": "probe"}])

    async def _probe(ctx: Any) -> None:
        ctx.result.update({"action": "nothing_to_do"})

    _patch_registry(mocker, {"probe": _probe})

    result = await _task(redis_async).execute("bs1")

    assert result.success is True


@pytest.mark.asyncio
async def test_optional_true_downgrades_failure(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    _write_exec_repo(
        tmp_path, [{"exec": "probe", "optional": True}, {"exec": "after"}]
    )
    ran: list[str] = []

    async def _probe(ctx: Any) -> None:
        ran.append("probe")
        ctx.fail("panel_not_opened")

    async def _after(ctx: Any) -> None:
        ran.append("after")

    _patch_registry(mocker, {"probe": _probe, "after": _after})

    result = await _task(redis_async).execute("bs1")

    assert result.success is True
    assert ran == ["probe", "after"]


@pytest.mark.asyncio
async def test_raising_handler_fails_the_step(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    """A handler that raises is a bug, not a game state — it must not read ok."""
    _write_exec_repo(tmp_path, [{"exec": "probe"}])

    async def _probe(ctx: Any) -> None:
        msg = "boom"
        raise RuntimeError(msg)

    _patch_registry(mocker, {"probe": _probe})

    result = await _task(redis_async).execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "exec_failed"


@pytest.mark.asyncio
async def test_unknown_exec_name_fails_the_step(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    """A typo'd ``exec:`` name used to be swallowed as a successful no-op."""
    _write_exec_repo(tmp_path, [{"exec": "no_such_handler_anywhere"}])

    result = await _task(redis_async).execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "unknown_exec"


@pytest.mark.asyncio
async def test_nested_exec_failure_also_ends_the_scenario(
    tmp_path: Path, mocker: Any, redis_async: object, _blank_actions: Any
) -> None:
    """The nested step interpreter is a separate code path — pin it too."""
    _write_exec_repo(
        tmp_path,
        [{"loop": {"max": 3, "steps": [{"exec": "probe"}]}}, {"exec": "after"}],
    )
    ran: list[str] = []

    async def _probe(ctx: Any) -> None:
        ran.append("probe")
        ctx.fail("marksman_row_not_found")

    async def _after(ctx: Any) -> None:
        ran.append("after")

    _patch_registry(mocker, {"probe": _probe, "after": _after})

    result = await _task(redis_async).execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "marksman_row_not_found"
    # One iteration, then the scenario ends — no second loop pass, no `after`.
    assert ran == ["probe"]
