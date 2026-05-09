from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 100, 100

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        return np.zeros((100, 100, 3), dtype=np.uint8)


@pytest.mark.asyncio
async def test_resume_from_step_skips_root_node_navigation(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    (tmp_path / "scenarios" / "chapters").mkdir(parents=True)
    (tmp_path / "scenarios" / "chapters" / "resume_router.yaml").write_text(
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
        raise AssertionError("root node navigation should be skipped on resumed steps")

    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())
    monkeypatch.setattr(dsl.DslScenarioTask, "_navigate_to_node", _fail_navigation)

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
    monkeypatch: Any,
    redis_async: object,
) -> None:
    (tmp_path / "scenarios" / "chapters").mkdir(parents=True)
    (tmp_path / "scenarios" / "chapters" / "done_router.yaml").write_text(
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
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={
            "last_active_scenario": "done_router",
            "last_active_scenario_priority": "70000",
            "last_active_scenario_player": "765502864",
            "last_active_scenario_step": "0",
        },
    )

    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: _FakeActions())

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="765502864",
        scenario_key="done_router",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    state = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]
    assert state["last_active_scenario"] == ""
    assert state["last_active_scenario_step"] == ""
