from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    def __init__(self, *, approve: bool = True) -> None:
        self.approve = approve
        self.system_backs: list[str] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 1000, 1000

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        return np.zeros((1000, 1000, 3), dtype=np.uint8)

    def system_back(self, instance_id: str) -> bool:
        self.system_backs.append(instance_id)
        return self.approve


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
    monkeypatch: Any,
    redis_async: object,
) -> None:
    _write_scenario(
        tmp_path,
        [
            {"system_back": True},
            {"steps": [{"system_back": True}]},
        ],
    )
    actions = _FakeActions()
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="system_back_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is True
    assert actions.system_backs == ["bs1", "bs1"]


@pytest.mark.asyncio
async def test_dsl_system_back_rejection_aborts_scenario(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    _write_scenario(tmp_path, [{"system_back": True}])
    actions = _FakeActions(approve=False)
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="system_back_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is False
    assert res.metadata["reason"] == "system_back_not_approved"
    assert actions.system_backs == ["bs1"]
