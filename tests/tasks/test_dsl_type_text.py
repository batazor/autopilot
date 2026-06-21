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


def _write_scenario(tmp_path: Path, steps: list[dict[str, Any]]) -> None:
    module_dir = (
        _modules_root_for(_default_game(), repo_root=tmp_path)
        / "core"
        / "test_scenarios"
    )
    scenario_root = module_dir / "scenarios"
    scenario_root.mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "type_text_demo.yaml").write_text(
        yaml.dump({"enabled": True, "steps": steps}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump({"screens": [{"id": 1, "ocr": "references/x.png", "regions": []}]}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_dsl_type_text_runs_top_level_and_nested(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_scenario(
        tmp_path,
        [
            {"type_text": "50"},
            {"steps": [{"type_text": "20"}]},
        ],
    )
    typed: list[tuple[str, str]] = []
    actions = make_actions(resolution=(1000, 1000))
    actions.type_text.side_effect = (
        lambda instance_id, text: typed.append((instance_id, text)) or True
    )
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="type_text_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is True
    assert typed == [("bs1", "50"), ("bs1", "20")]


@pytest.mark.asyncio
async def test_dsl_type_text_rejection_aborts_scenario(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    _write_scenario(tmp_path, [{"type_text": "50"}])
    actions = make_actions(resolution=(1000, 1000))
    actions.type_text.return_value = False
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="type_text_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is False
    assert res.metadata["reason"] == "type_text_not_approved"
