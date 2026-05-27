from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for
from tasks.base import TaskResult

if TYPE_CHECKING:
    from pathlib import Path


def _scenario_root(tmp_path: Path) -> Path:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    return scenario_root


@pytest.mark.asyncio
async def test_resume_from_step_skips_root_node_navigation(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "chapters").mkdir(parents=True)
    (scenario_root / "chapters" / "resume_router.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Resume router",
                "node": "main_city",
                "steps": [
                    {"click": "chapter.task"},
                    {"wait": "0s"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")

    async def _fail_navigation(*_args: Any, **_kwargs: Any) -> bool:
        msg = "root node navigation should be skipped on resumed steps"
        raise AssertionError(msg)

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)
    mocker.patch.object(dsl.DslScenarioTask, "_navigate_to_node", new=_fail_navigation)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="resume_router",
        start_step_index=1,
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert result.metadata["scenario_completed"] is True
    assert result.metadata["resume_from_step_index"] == 1


@pytest.mark.asyncio
async def test_completed_scenario_clears_stale_hand_pointer_resume(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "chapters").mkdir(parents=True)
    (scenario_root / "chapters" / "done_router.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Done router",
                "steps": [{"wait": "0s"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={
            "last_active_scenario": "done_router",
            "last_active_scenario_priority": "70000",
            "last_active_scenario_player": "765502864",
            "last_active_scenario_step": "0",
        },
    )

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="765502864",
        scenario_key="done_router",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    state = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    assert state["last_active_scenario"] == ""
    assert state["last_active_scenario_step"] == ""


@pytest.mark.asyncio
async def test_navigation_failed_records_trace_row(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Navigation failure must leave at least one trace row explaining itself.

    Before the fix, the navigation_failed return path skipped ``_trace_row``,
    producing TaskResult metadata with an empty ``steps_trace`` — the UI
    showed "0 steps ran" with no hint of what went wrong.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "chapters").mkdir(parents=True)
    (scenario_root / "chapters" / "nav_fail.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Nav fail",
                "node": "event.trials",
                "steps": [{"wait": "0s"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")

    # Seed current_screen so the trace row can capture it before the
    # navigation_failed branch blanks it.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", "current_screen", "popup.claim"
    )

    async def _navfail(*_args: Any, **_kwargs: Any) -> bool:
        return False

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)
    mocker.patch.object(dsl.DslScenarioTask, "_navigate_to_node", new=_navfail)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="nav_fail",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    assert result.metadata["reason"] == "navigation_failed"
    trace = result.metadata["steps_trace"]
    assert len(trace) == 1, f"expected single explanatory row, got {trace!r}"  # ty: ignore[invalid-argument-type]
    row = trace[0]  # ty: ignore[not-subscriptable]
    assert row["status"] == "early_exit"
    assert row["reason"] == "navigation_failed"
    assert row["target"] == "event.trials"
    assert row["current_screen"] == "popup.claim"


@pytest.mark.asyncio
async def test_preempt_yield_with_target_node_resumes_at_actual_step(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Preempt-yield must resume at the actual step, not reset to 0.

    Regression guard for the original ``claim_trials`` bug: scenarios with
    ``node:`` used to reset ``resume_from_step_index`` to 0 on preempt, which
    re-fired the navigation gate on resume. When a mid-scenario popup had
    blanked ``current_screen``, that BFS failed and the whole scenario aborted
    with ``navigation_failed`` even though earlier steps had completed.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "chapters").mkdir(parents=True)
    (scenario_root / "chapters" / "preempt_router.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Preempt router",
                "node": "event.trials",
                "steps": [{"wait": "0s"}, {"wait": "0s"}, {"wait": "0s"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")

    async def _navok(*_args: Any, **_kwargs: Any) -> bool:
        return True

    async def _yield_at_step_1(
        self: Any, instance_id: str, step_index: int
    ) -> TaskResult | None:
        if step_index >= 1:
            return TaskResult(
                success=False,
                next_run_at=None,
                metadata={"reason": "preempted_by_higher_priority"},
            )
        return None

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)
    mocker.patch.object(dsl.DslScenarioTask, "_navigate_to_node", new=_navok)
    mocker.patch.object(
        dsl.DslScenarioTask,
        "_preempted_by_higher_priority",
        new=_yield_at_step_1,
    )

    # Seed ``current_screen`` so the entry-time screen-identity gate (see
    # ``DslScenarioExecuteMixin.execute``) lets the scenario proceed to its
    # first step. Empty ``current_screen`` would otherwise short-circuit
    # before the preempt-yield logic under test ever runs.
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", "current_screen", "event.trials"
    )

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="preempt_router",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    # The fix: resume at the actually-yielded step, not 0.
    assert result.metadata["resume_from_step_index"] == 1
    # And the trace was persisted to Redis so the resumed slice can hydrate.
    raw = await redis_async.hget(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", "last_active_scenario_trace"
    )
    assert raw, "preempt-yield must persist steps_trace for resume"
    persisted = json.loads(raw)
    assert isinstance(persisted, list) and persisted
    assert persisted[-1]["reason"] == "preempted_by_higher_priority"
    assert persisted[-1]["i"] == "1"


@pytest.mark.asyncio
async def test_resume_hydrates_trace_from_prior_slice(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Resume must rehydrate ``steps_trace`` from the prior slice's persisted
    history, so the resumed TaskResult shows the full scenario story rather
    than only what happened after resume.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "chapters").mkdir(parents=True)
    (scenario_root / "chapters" / "resume_trace.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Resume trace",
                "node": "event.trials",
                "steps": [{"wait": "0s"}, {"wait": "0s"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")

    prior = [
        {"i": "0", "summary": "wait: 0s", "status": "ok"},
        {
            "i": "1",
            "summary": "wait: 0s",
            "status": "preempted",
            "reason": "preempted_by_higher_priority",
        },
    ]
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        "last_active_scenario_trace",
        json.dumps(prior),
    )

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="resume_trace",
        start_step_index=1,
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    trace = result.metadata["steps_trace"]
    assert len(trace) >= 3  # ty: ignore[invalid-argument-type]
    assert trace[0] == prior[0]  # ty: ignore[not-subscriptable]
    assert trace[1] == prior[1]  # ty: ignore[not-subscriptable]
    # Successful completion wipes the persisted trace via _clear_step_context.
    raw = await redis_async.hget(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state", "last_active_scenario_trace"
    )
    assert raw in (None, "")


@pytest.mark.asyncio
async def test_nested_steps_emit_trace_rows_with_path_indices(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Container steps (repeat / while_match) must record per-iteration
    markers and per-leaf-step rows so the trace shows what happened inside.

    Before this change, only the top-level container row was recorded —
    nested clicks and inner ``while_match`` iterations were invisible.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "chapters").mkdir(parents=True)
    (scenario_root / "chapters" / "nested.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Nested",
                "steps": [
                    {
                        "repeat": {
                            "max": 2,
                            "steps": [
                                {"wait": "0s"},
                                {"wait": "0s"},
                            ],
                        }
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")

    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="nested",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    trace = result.metadata["steps_trace"]

    triples = [(r["i"], r["summary"], r["status"]) for r in trace]  # ty: ignore[not-iterable]

    # Per-iteration marker rows.
    assert ("0.0", "iter 0", "iter") in triples
    assert ("0.1", "iter 1", "iter") in triples
    # Per-leaf-step rows inside each iteration.
    assert ("0.0.0", "wait:0s", "ok") in triples
    assert ("0.0.1", "wait:0s", "ok") in triples
    assert ("0.1.0", "wait:0s", "ok") in triples
    assert ("0.1.1", "wait:0s", "ok") in triples
    # Final top-level aggregate row with iterations count.
    repeat_rows = [r for r in trace if r["i"] == "0" and r["status"] == "ok"]  # ty: ignore[not-iterable]
    assert len(repeat_rows) == 1
    assert repeat_rows[0]["iterations"] == 2


def test_step_summary_distinguishes_guards() -> None:
    """Steps with different guard flags must render distinctly in the trace
    summary. Without this, e.g. claim_trials' ``while_match: button.claim``
    appears twice with identical summaries — the plain variant and the
    ``isWhiteBorder: true`` variant become indistinguishable.
    """
    from tasks.dsl_scenario_helpers import _dsl_step_summary

    plain = _dsl_step_summary({"while_match": "button.claim"})
    white = _dsl_step_summary(
        {"while_match": "button.claim", "isWhiteBorder": True}
    )
    red = _dsl_step_summary({"while_match": "button.claim", "isRedDot": True})
    assert plain == "while_match:button.claim"
    assert white == "while_match:button.claim [isWhiteBorder]"
    assert red == "while_match:button.claim [isRedDot]"
