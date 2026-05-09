from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

import tasks.dsl_scenario as dsl


class _FakeActions:
    def __init__(self) -> None:
        self.long_taps: list[tuple[str, int, int, int]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 1000, 1000

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        return np.zeros((1000, 1000, 3), dtype=np.uint8)

    def long_tap(self, instance_id: str, point: Any, duration_ms: int = 800) -> bool:
        self.long_taps.append((instance_id, int(point.x), int(point.y), int(duration_ms)))
        return True

    def tap(self, *_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("tap() should not be called in long_click test")


@pytest.mark.asyncio
async def test_dsl_long_click_uses_wait_as_duration(tmp_path: Path, monkeypatch: Any, redis_async: object) -> None:
    (tmp_path / "scenarios" / "building").mkdir(parents=True)
    (tmp_path / "scenarios" / "building" / "long_click_demo.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "steps": [{"long_click": "upgrade_button", "wait": "5s"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/x.png",
                        "regions": [
                            {
                                "name": "upgrade_button",
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    actions = _FakeActions()
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(dsl, "BotActions", lambda: actions)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="long_click_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    res = await task.execute("bs1")
    assert res.success is True
    assert actions.long_taps == [("bs1", 150, 150, 5000)]

