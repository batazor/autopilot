from __future__ import annotations

import json
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
async def test_dsl_long_click_uses_wait_as_duration(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
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


def test_dsl_long_click_point_reuses_last_match_tap_percent(redis_async: object) -> None:
    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="long_click_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    task._last_match_region = "upgrade_button"
    task._last_match_row = {
        "matched": True,
        "tap_x_pct": 84.375,
        "tap_y_pct": 50.6641,
    }

    pt = task._point_for_region_action(
        "upgrade_button",
        {"x": 74.0, "y": 40.0, "width": 20.0, "height": 3.0},
        720,
        1280,
    )

    assert (pt.x, pt.y) == (608, 649)


@pytest.mark.asyncio
async def test_dsl_missing_scenario_pushes_ui_notification(
    tmp_path: Path,
    monkeypatch: Any,
    redis_async: object,
) -> None:
    (tmp_path / "scenarios").mkdir()
    monkeypatch.setattr(dsl, "_repo_root", lambda: tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="missing_upgrade",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    res = await task.execute("bs1")

    assert res.success is False
    assert res.metadata == {"reason": "scenario_not_found", "key": "missing_upgrade"}
    raw = await redis_async.lrange("wos:ui:notifications:bs1", 0, -1)  # type: ignore[attr-defined]
    assert len(raw) == 1
    body = json.loads(raw[0])
    assert body["kind"] == "dsl.scenario_not_found"
    assert body["level"] == "error"
    assert body["message"] == "Scenario not found: missing_upgrade"
