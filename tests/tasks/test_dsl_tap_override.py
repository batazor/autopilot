from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl


def _scenario_root(tmp_path: Path) -> Path:
    mod = tmp_path / "modules" / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    return scenario_root


@pytest.mark.asyncio
async def test_dsl_click_uses_overlay_tap_override(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "onboarding").mkdir(parents=True)
    (scenario_root / "onboarding" / "hand_pointer.yaml").write_text(
        yaml.dump({"enabled": True, "name": "Hand", "steps": [{"click": "hand_pointer"}]}),
        encoding="utf-8",
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/tutorial.png",
                        "regions": [
                            {
                                "name": "hand_pointer",
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    actions = make_actions(resolution=(1000, 1000))
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="hand_pointer",
        redis_client=redis_async,  # type: ignore[arg-type]
        tap_region="hand_pointer",
        tap_x_pct=50.0,
        tap_y_pct=70.0,
    )

    result = await task.execute("bs1")

    assert result.success is True
    tap_call = actions.tap.call_args_list[0]
    assert tap_call[0][0] == "bs1"
    assert tap_call[0][1].x == 500
    assert tap_call[0][1].y == 700
    assert tap_call[1]["approval_region"] == "hand_pointer"


@pytest.mark.asyncio
async def test_dsl_click_forwards_min_match_saturation_to_implicit_match(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """``click: foo / min_match_saturation: 40`` reaches the implicit search rule.

    Without forwarding, click steps with `_search` companions would fall back to
    the engine default and ignore user-set saturation gates.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "main_city").mkdir(parents=True)
    (scenario_root / "main_city" / "tap_with_sat.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Tap with saturation",
                "device_level": True,
                "steps": [
                    {
                        "click": "claim_btn",
                        "min_match_saturation": 40,
                        "threshold": 0.97,
                    }
                ],
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
                                "name": "claim_btn",
                                "action": "exist",
                                "type": "string",
                                "threshold": 0.9,
                                "isSearch": True,
                                "bbox": {"x": 10, "y": 10, "width": 10, "height": 10},
                            },
                            # Search companion → triggers implicit match-on-click.
                            {
                                "name": "claim_btn_search",
                                "action": "exist",
                                "type": "string",
                                "threshold": 0.9,
                                "bbox": {"x": 0, "y": 0, "width": 100, "height": 100},
                                "overlay_auxiliary": True,
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    captured_rule: dict[str, Any] = {}

    async def _fake_eval(
        _image_bgr: Any, _area_doc: Any, _repo_root: Any, rules: Any, **_kw: Any
    ) -> Any:
        # Stash the rule the engine sees so the test can inspect forwarded keys.
        captured_rule.clear()
        captured_rule.update(rules[0])
        name = str(rules[0].get("name") or "")
        return {
            name: {
                "matched": True,
                "score": 0.99,
                "threshold": 0.97,
                "tap_x_pct": 50.0,
                "tap_y_pct": 60.0,
                "top_left": [10, 10],
                "template_w": 50,
                "template_h": 50,
            }
        }

    actions = make_actions(np.zeros((1000, 1000, 3), dtype=np.uint8), resolution=(1000, 1000))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    mocker.patch.object(dsl, "evaluate_overlay_rules_async", new=_fake_eval)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="tap_with_sat",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")
    assert result.success is True
    assert captured_rule.get("min_match_saturation") == 40
    assert captured_rule.get("threshold") == pytest.approx(0.97)
