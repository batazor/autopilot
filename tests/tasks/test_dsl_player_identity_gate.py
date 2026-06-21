"""Implicit ``player_id`` identity gate at scenario start.

Scenarios without ``device_level: true`` are skipped when the queue item
carries ``player_id=""`` — that's the contract ``who_i_am`` (the only
canonical ``device_level: true`` scenario) establishes by writing
``active_player`` to instance state. Centralising this gate means every
downstream Redis-touching helper can assume a non-empty ``player_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from config.games import default_game as _default_game
from config.games import modules_root_for as _modules_root_for

if TYPE_CHECKING:
    from pathlib import Path


def _write_scenario(tmp_path: Path, doc: dict[str, Any]) -> None:
    mod = _modules_root_for(_default_game(), repo_root=tmp_path) / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "scn.yaml").write_text(
        yaml.dump({"enabled": True, "name": "scn", **doc}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(yaml.dump({"screens": []}), encoding="utf-8")


@pytest.mark.asyncio
async def test_player_bound_scenario_skipped_when_player_id_empty(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Plain (non-device-level) scenario + empty player_id → skip with
    ``awaiting_player_identity``. No navigation, no steps, no taps."""
    _write_scenario(tmp_path, {"steps": [{"exec": "should_not_run"}]})
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    fired = {"count": 0}

    async def _should_not_run(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"should_not_run": _should_not_run})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert result.metadata["reason"] == "awaiting_player_identity"
    assert result.metadata["scenario_completed"] is True
    assert fired["count"] == 0


@pytest.mark.asyncio
async def test_device_level_scenario_runs_with_empty_player_id(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """``device_level: true`` opts out of the gate — that's how ``who_i_am``
    itself bootstraps identity."""
    _write_scenario(
        tmp_path,
        {"device_level": True, "steps": [{"exec": "bootstrap"}]},
    )
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    fired = {"count": 0}

    async def _bootstrap(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"bootstrap": _bootstrap})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert result.metadata.get("reason") != "awaiting_player_identity"
    assert fired["count"] == 1


@pytest.mark.asyncio
async def test_exec_result_is_added_to_steps_trace(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Exec handlers can expose no-op/tap diagnostics on the scenario trace."""
    _write_scenario(
        tmp_path,
        {"device_level": True, "steps": [{"exec": "diagnose"}]},
    )
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    async def _diagnose(ctx: Any) -> None:
        ctx.result.update(
            {
                "reason": "no_popup",
                "popup_action": "clear",
                "status": "no_op",
            }
        )

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"diagnose": _diagnose})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    trace = result.metadata["steps_trace"]
    assert trace[0]["summary"] == "exec:diagnose"
    assert trace[0]["status"] == "ok"
    assert trace[0]["exec_status"] == "no_op"
    assert trace[0]["reason"] == "no_popup"
    assert trace[0]["popup_action"] == "clear"


@pytest.mark.asyncio
async def test_player_bound_scenario_runs_with_explicit_player_id(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Non-device-level scenario + non-empty player_id → normal execution."""
    _write_scenario(tmp_path, {"steps": [{"exec": "do_work"}]})
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    fired = {"count": 0}

    async def _do_work(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"do_work": _do_work})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="765502864",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert result.metadata.get("reason") != "awaiting_player_identity"
    assert fired["count"] == 1


@pytest.mark.asyncio
async def test_node_bound_scenario_retries_when_screen_identity_empty(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """A node-bound scenario should not be consumed when screen identity is blank.

    The queue item has already been popped by the time the DSL preflight runs, so
    the task must return ``next_run_at`` to be re-enqueued instead of reporting a
    successful no-op.
    """

    _write_scenario(tmp_path, {"node": "event.trials.day.1", "steps": [{"exec": "do_work"}]})
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    fired = {"count": 0}

    async def _do_work(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"do_work": _do_work})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="765502864",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    assert result.next_run_at is not None
    assert result.metadata["reason"] == "awaiting_screen_identity"
    assert result.metadata["scenario_completed"] is False
    assert fired["count"] == 0


@pytest.mark.asyncio
async def test_node_bound_scenario_recovers_screen_from_history(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """When ``current_screen`` is transiently blank but the device is still on
    the screen we last confirmed, the preflight must recover identity from
    ``screen_history`` instead of burning the task on
    ``awaiting_screen_identity``. Recovery republishes the screen, so when the
    recovered node is one of the scenario's allowed nodes the steps run in
    place — no 5s re-queue hot loop."""

    _write_scenario(
        tmp_path, {"node": "event.trials.day.1", "steps": [{"exec": "do_work"}]}
    )
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    from navigation.navigator import Navigator

    async def _no_detect(_self: Any, *_args: Any, **_kwargs: Any) -> str:
        return ""

    async def _recover(_self: Any, *_args: Any, **_kwargs: Any) -> str:
        return "event.trials.day.1"

    mocker.patch.object(Navigator, "detect_current_screen", new=_no_detect)
    mocker.patch.object(Navigator, "recover_screen_from_history", new=_recover)

    fired = {"count": 0}

    async def _do_work(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"do_work": _do_work})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="765502864",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.metadata.get("reason") != "awaiting_screen_identity"
    assert fired["count"] == 1


@pytest.mark.asyncio
async def test_device_level_node_bound_scenario_retries_when_screen_identity_empty(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """A ``device_level: true`` scenario that declares ``node:`` must still be
    gated on screen identity. Navigation cannot route from an unknown source,
    so burning the attempt only feeds a hot retry loop — ``who_i_am`` is the
    motivating case (overlay-pushed every rolling tick while
    ``active_player`` stays empty)."""

    _write_scenario(
        tmp_path,
        {
            "device_level": True,
            "node": "chief_profile",
            "steps": [{"exec": "should_not_run"}],
        },
    )
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    fired = {"count": 0}

    async def _should_not_run(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"should_not_run": _should_not_run})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is False
    assert result.next_run_at is not None
    assert result.metadata["reason"] == "awaiting_screen_identity"
    assert result.metadata["scenario_completed"] is False
    assert fired["count"] == 0


@pytest.mark.asyncio
async def test_gate_skipped_on_resume(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Cooperative preemption + resume must not re-trigger the gate."""
    _write_scenario(tmp_path, {"steps": [{"exec": "resumed"}]})
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    fired = {"count": 0}

    async def _resumed(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"resumed": _resumed})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="",
        scenario_key="scn",
        start_step_index=1,
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.metadata.get("reason") != "awaiting_player_identity"
