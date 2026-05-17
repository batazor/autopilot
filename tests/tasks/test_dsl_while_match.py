from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, call

import cv2
import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl


def _claim_pattern() -> np.ndarray:
    patch = np.zeros((10, 10, 3), dtype=np.uint8)
    patch[:] = (20, 160, 240)
    patch[2:8, 2:8] = (20, 220, 40)
    patch[4:6, :] = (255, 255, 255)
    return patch


def _scenario_root(tmp_path: Path) -> Path:
    mod = tmp_path / "modules" / "core" / "test_scenarios"
    mod.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    scen = mod / "scenarios"
    scen.mkdir(exist_ok=True)
    return scen


def _write_claim_repo(tmp_path: Path, frame: np.ndarray) -> None:
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "overlay").mkdir(parents=True)
    (tmp_path / "references" / "crop").mkdir(parents=True)
    (scenario_root / "overlay" / "tap_claim_button.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Claim",
                "steps": [
                    {
                        "while_match": "button.claim",
                        "threshold": 0.98,
                        "max": 5,
                        "steps": [
                            {"click": "button.claim"},
                            {"wait": 0},
                        ],
                    },
                    {"click": "claim_button_close"},
                ],
            }
        ),
        encoding="utf-8",
    )
    cv2.imwrite(str(tmp_path / "references/crop/claim_button.claim.png"), frame[20:30, 20:30])
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/claim.png",
                        "regions": [
                            {
                                "name": "button.claim",
                                "bbox": {"x": 20, "y": 20, "width": 10, "height": 10},
                            },
                            {
                                "name": "claim_button_close",
                                "bbox": {"x": 80, "y": 10, "width": 10, "height": 10},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_dsl_while_match_clicks_until_region_disappears_then_closes(
    tmp_path: Path,
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    visible = np.zeros((100, 100, 3), dtype=np.uint8)
    visible[20:30, 20:30] = _claim_pattern()
    gone = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_claim_repo(tmp_path, visible)
    actions = make_actions([visible, visible, gone], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="tap_claim_button",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region="button.claim"),
        call("bs1", ANY, approval_region="button.claim"),
        call("bs1", ANY, approval_region="claim_button_close"),
    ]


@pytest.mark.asyncio
async def test_nested_while_match_retry_waits_for_late_region(
    tmp_path: Path,
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    visible = np.zeros((100, 100, 3), dtype=np.uint8)
    visible[20:30, 20:30] = _claim_pattern()
    gone = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_claim_repo(tmp_path, visible)
    scenario_path = (
        tmp_path / "modules" / "core" / "test_scenarios" / "scenarios" / "overlay" / "tap_claim_button.yaml"
    )
    scenario_path.write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Claim nested retry",
                "steps": [
                    {
                        "loop": {
                            "max": 1,
                            "steps": [
                                {
                                    "while_match": "button.claim",
                                    "threshold": 0.98,
                                    "max": 1,
                                    "retry": {"attempts": 3, "interval": 0},
                                    "steps": [{"click": "button.claim"}],
                                },
                                {"click": "claim_button_close"},
                            ],
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    actions = make_actions([gone, gone, visible], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="tap_claim_button",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region="button.claim"),
        call("bs1", ANY, approval_region="claim_button_close"),
    ]


def _write_repo_with_else(tmp_path: Path, frame: np.ndarray) -> None:
    """Scenario with ``while_match`` + ``else:`` fallback steps.

    Two regions: the primary ``button.claim`` (probed by ``while_match``) and
    a fallback ``button.fallback`` that is clicked from the ``else:`` branch
    when the primary never matches.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "overlay").mkdir(parents=True)
    (tmp_path / "references" / "crop").mkdir(parents=True)
    (scenario_root / "overlay" / "tap_with_else.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "device_level": True,
                "name": "Claim or fallback",
                "steps": [
                    {
                        "while_match": "button.claim",
                        "threshold": 0.98,
                        "max": 5,
                        "steps": [{"click": "button.claim"}],
                        "else": [{"click": "button.fallback"}],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    # Reference patch matches the visible-frame variant so a "gone" frame fails to match.
    cv2.imwrite(
        str(tmp_path / "references/crop/claim_button.claim.png"),
        frame[20:30, 20:30],
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/claim.png",
                        "regions": [
                            {
                                "name": "button.claim",
                                "bbox": {"x": 20, "y": 20, "width": 10, "height": 10},
                            },
                            {
                                "name": "button.fallback",
                                "bbox": {"x": 60, "y": 60, "width": 10, "height": 10},
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_dsl_while_match_runs_else_branch_when_no_iterations(
    tmp_path: Path,
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """Zero iterations + ``else:`` → fallback steps run, scenario succeeds."""
    visible = np.zeros((100, 100, 3), dtype=np.uint8)
    visible[20:30, 20:30] = _claim_pattern()
    gone = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_repo_with_else(tmp_path, visible)
    # Only "gone" frames — primary never matches; else-branch should run.
    actions = make_actions([gone, gone, gone], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="tap_with_else",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    # Else-branch tapped the fallback region (centered at 65,65).
    assert actions.tap.call_args_list == [call("bs1", ANY, approval_region="button.fallback")]


@pytest.mark.asyncio
async def test_dsl_while_match_skips_else_when_iterations_ran(
    tmp_path: Path,
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """Loop body ran at least once → ``else:`` is skipped entirely."""
    visible = np.zeros((100, 100, 3), dtype=np.uint8)
    visible[20:30, 20:30] = _claim_pattern()
    gone = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_repo_with_else(tmp_path, visible)
    actions = make_actions([visible, gone], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="tap_with_else",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    # Only the primary tap recorded — fallback wasn't touched.
    assert actions.tap.call_args_list == [call("bs1", ANY, approval_region="button.claim")]


def test_tap_claim_button_while_match_has_nested_steps() -> None:
    repo = Path(__file__).resolve().parents[2]
    scenario_path = repo / "modules/core/pop-up/scenarios/tap_claim_button.yaml"
    if not scenario_path.is_file():
        pytest.skip("legacy pop-up tap_claim_button scenario removed")
    doc = yaml.safe_load(scenario_path.read_text())
    loop = doc["steps"][0]

    assert loop["while_match"] == "button.claim"
    assert loop["steps"] == [{"click": "button.claim"}, {"wait": "3s"}]
    close = doc["steps"][1]
    assert close["while_match"] == "claim_button_close"
    assert close["max"] == 1
    assert close["steps"] == [{"click": "claim_button_close"}, {"wait": "1s"}]
