from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl


def _write_scenario(tmp_path: Path, steps: list[dict[str, Any]]) -> None:
    module_dir = tmp_path / "modules" / "core" / "test_scenarios"
    scenario_root = module_dir / "scenarios"
    (scenario_root / "test").mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "test" / "system_back_demo.yaml").write_text(
        yaml.dump({"enabled": True, "steps": steps}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump({"screens": [{"id": 1, "ocr": "references/x.png", "regions": []}]}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_dsl_system_back_runs_top_level_and_nested(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_scenario(
        tmp_path,
        [
            {"system_back": True},
            {"steps": [{"system_back": True}]},
        ],
    )
    system_backs: list[str] = []
    actions = make_actions(resolution=(1000, 1000))
    actions.system_back.side_effect = lambda instance_id: system_backs.append(instance_id) or True
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="system_back_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is True
    assert system_backs == ["bs1", "bs1"]


@pytest.mark.asyncio
async def test_dsl_system_back_rejection_aborts_scenario(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_scenario(tmp_path, [{"system_back": True}])
    system_backs: list[str] = []
    actions = make_actions(resolution=(1000, 1000))
    actions.system_back.side_effect = lambda instance_id: system_backs.append(instance_id) or False
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="system_back_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is False
    assert res.metadata["reason"] == "system_back_not_approved"
    assert system_backs == ["bs1"]
