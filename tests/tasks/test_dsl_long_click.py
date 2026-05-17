from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml

from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl


@pytest.mark.asyncio
async def test_dsl_long_click_uses_wait_as_duration(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    module_dir = tmp_path / "modules" / "core" / "test_scenarios"
    scenario_root = module_dir / "scenarios"
    (scenario_root / "building").mkdir(parents=True)
    (module_dir / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    (scenario_root / "building" / "long_click_demo.yaml").write_text(
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

    actions = make_actions()
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="long_click_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    res = await task.execute("bs1")
    assert res.success is True
    # bbox 10..20% on a 1000x1000 screen → 100..200 px. With the 15% inset
    # applied by the random-point helper the long-tap lands inside [115, 185]
    # on both axes (±1 px rounding tolerance).
    assert len(actions.long_tap.call_args_list) == 1
    inst, point, dur = (
        actions.long_tap.call_args_list[0][0][0],
        actions.long_tap.call_args_list[0][0][1],
        actions.long_tap.call_args_list[0][1]["duration_ms"],
    )
    assert inst == "bs1"
    assert dur == 5000
    assert 114 <= point.x <= 186, point.x
    assert 114 <= point.y <= 186, point.y
    actions.tap.assert_not_called()


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

    # No template_w/h on the match row → return the exact tap percent point.
    assert (pt.x, pt.y) == (608, 649)


def test_dsl_click_randomises_inside_matched_template(redis_async: object) -> None:
    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="long_click_demo",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    task._last_match_region = "upgrade_button"
    # Matched template: 80 px × 40 px on a 720×1280 framebuffer, centred at
    # (84.375%, 50.6641%) → (607.5, 648.5) px ⇒ click zone roughly
    # x ∈ [567.5, 647.5], y ∈ [628.5, 668.5]. After the 15% inset the random
    # point must stay inside the shrunk window.
    task._last_match_row = {
        "matched": True,
        "tap_x_pct": 84.375,
        "tap_y_pct": 50.6641,
        "template_w": 80,
        "template_h": 40,
    }

    seen = set()
    for _ in range(80):
        pt = task._point_for_region_action(
            "upgrade_button",
            {"x": 74.0, "y": 40.0, "width": 20.0, "height": 3.0},
            720,
            1280,
        )
        seen.add((pt.x, pt.y))
        assert 575 <= pt.x <= 640, pt
        assert 634 <= pt.y <= 663, pt

    assert len(seen) > 5  # actually randomised, not pinned


@pytest.mark.asyncio
async def test_dsl_missing_scenario_pushes_ui_notification(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    patch_dsl(mocker, make_actions(), repo_root=tmp_path)

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
