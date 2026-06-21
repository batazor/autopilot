"""``navigate: false`` — opportunistic helpers run in place, never travel.

``tabs.strip.advance`` declares ``nodes: [shop, …, deals, …]`` only to say where
advancing a tab strip is meaningful. The analyzer pushes it from a Shop/Deals
sub-page, but the queued task can outlive the visit. Popped on ``main_city`` it
used to navigate back into Shop purely to flip a tab. ``navigate: false`` makes
the runtime run the steps in place when already on an allowed node and skip with
a benign success otherwise — it must never drive the FSM to reach the nodes.
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
async def test_navigate_false_off_node_skips_without_travelling(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Off an allowed node → benign success, no steps, no navigation."""
    _write_scenario(
        tmp_path,
        {
            "navigate": False,
            "nodes": ["shop", "shop.daily_deals"],
            "steps": [{"exec": "should_not_run"}],
        },
    )
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    # Bot sat back on main_city by the time the stale task was popped.
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state", "current_screen", "main_city"
    )

    fired = {"count": 0}

    async def _should_not_run(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"should_not_run": _should_not_run})

    # _navigate_to_node must never be reached for a navigate:false helper.
    nav_spy = mocker.patch.object(
        dsl.DslScenarioTask,
        "_navigate_to_node",
        autospec=True,
    )

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="765502864",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert result.metadata["reason"] == "off_node_navigate_disabled"
    assert result.metadata["current_screen"] == "main_city"
    assert result.metadata["scenario_completed"] is True
    assert fired["count"] == 0
    nav_spy.assert_not_called()


@pytest.mark.asyncio
async def test_navigate_false_on_node_runs_in_place(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Already on an allowed node → steps run, still no navigation."""
    _write_scenario(
        tmp_path,
        {
            "navigate": False,
            "nodes": ["shop", "shop.daily_deals"],
            "steps": [{"exec": "do_work"}],
        },
    )
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state", "current_screen", "shop.daily_deals"
    )

    fired = {"count": 0}

    async def _do_work(_ctx: Any) -> None:
        fired["count"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"do_work": _do_work})

    nav_spy = mocker.patch.object(
        dsl.DslScenarioTask,
        "_navigate_to_node",
        autospec=True,
    )

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="765502864",
        scenario_key="scn",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert result.metadata.get("reason") != "off_node_navigate_disabled"
    assert fired["count"] == 1
    nav_spy.assert_not_called()
