"""Strict + retry semantics for top-level ``while_match`` in player-bound scenarios.

Player-bound DSL scenarios (no ``device_level: true`` marker) get:
- Initial-probe retries (default 5 × 500 ms) to absorb screen-settling lag
  after navigation, so a brief no-match after a tap doesn't kill the scenario.
- Strict zero-iteration failure: if the initial probe never matches even after
  retries, the scenario reschedules itself (success=False, next_run_at=+30 s)
  instead of silently yielding to the next queue item.

Device-level scenarios (popup dismissals like ``tap_claim_button``) keep the
legacy "0 iterations = success" behavior because their triggers may have
already been resolved by the time the task runs.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import ANY

import cv2
import numpy as np
import pytest
import yaml

from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl


def _patch_instant_sleep(mocker: Any) -> None:
    real_sleep = asyncio.sleep

    async def _instant_sleep(_s: float) -> None:
        await real_sleep(0)

    mocker.patch.object(dsl.asyncio, "sleep", _instant_sleep)


def _claim_pattern() -> np.ndarray:
    patch = np.zeros((10, 10, 3), dtype=np.uint8)
    patch[:] = (20, 160, 240)
    patch[2:8, 2:8] = (20, 220, 40)
    patch[4:6, :] = (255, 255, 255)
    return patch


def _scenario_root(tmp_path: Path) -> Path:
    mod = tmp_path / "modules" / "core" / "test_scenarios"
    scenario_root = mod / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    (mod / "module.yaml").write_text("id: test_scenarios\n", encoding="utf-8")
    return scenario_root


def _write_player_bound_scenario(tmp_path: Path, frame: np.ndarray) -> None:
    """A player-bound scenario opted into strict mode via explicit ``strict: true``.

    Scenario-level default is soft (steps are OR-semantics — zero iterations on
    a ``while_match`` just moves to the next step). These tests pin the
    *explicit strict* path: the user can still opt in to "this step MUST have
    done work" via YAML for rare gate-like steps.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "workers").mkdir(parents=True)
    (tmp_path / "references" / "crop").mkdir(parents=True)
    (scenario_root / "workers" / "test_assign.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "Test assign",
                "steps": [
                    {
                        "while_match": "page.worker.add",
                        "threshold": 0.9,
                        "max": 6,
                        "strict": True,
                        "steps": [
                            {"click": "page.worker.add"},
                            {"wait": 0},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    cv2.imwrite(
        str(tmp_path / "references/crop/page.worker.add_page.worker.add.png"),
        frame[20:30, 20:30],
    )
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/page.worker.add.png",
                        "regions": [
                            {
                                "name": "page.worker.add",
                                "bbox": {"x": 20, "y": 20, "width": 10, "height": 10},
                                "threshold": 0.9,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_player_bound_while_match_zero_iterations_returns_soft_failure(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """All probes miss → scenario reschedules itself instead of silent success."""
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, _frame_with_pattern())
    actions = make_actions([blank, blank, blank, blank, blank, blank, blank], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    mocker.patch.object(dsl, "click_approval_enabled", return_value=False)
    _patch_instant_sleep(mocker)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is False
    assert result.next_run_at is not None  # rescheduled
    assert (result.metadata or {}).get("reason") == "while_match_no_iterations"
    assert (result.metadata or {}).get("attempts") == 5  # default for player-bound
    assert actions.tap.call_args_list == []  # no clicks happened


@pytest.mark.asyncio
async def test_player_bound_while_match_zero_iterations_pauses_in_approval_mode(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, _frame_with_pattern())
    actions = make_actions([blank, blank, blank, blank, blank], resolution=(100, 100))
    approvals: list[tuple[str, dict[str, Any]]] = []
    patch_dsl(mocker, actions, repo_root=tmp_path)
    mocker.patch.object(dsl, "click_approval_enabled", return_value=True)
    mocker.patch.object(
        dsl,
        "_require_approval",
        side_effect=lambda instance_id, payload: approvals.append((instance_id, dict(payload)))
        or (True, None),
    )
    _patch_instant_sleep(mocker)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is False
    assert result.next_run_at is not None
    assert approvals
    instance_id, payload = approvals[0]
    assert instance_id == "bs1"
    assert payload["type"] == "diagnostic"
    assert payload["diagnostic"] == "while_match_no_iterations"
    assert payload["region"] == "page.worker.add"
    assert actions.attach_approval_preview.call_args_list[0][0][0] == "bs1"


@pytest.mark.asyncio
async def test_player_bound_while_match_initial_retry_eventually_matches(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """First two probes miss, third matches → click happens, scenario succeeds."""
    visible = _frame_with_pattern()
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, visible)
    # Sequence: blank, blank, visible, blank → retry exhausted on 3rd probe (matches),
    # click, then probe again (blank) → exit, iterations=1, success.
    actions = make_actions([blank, blank, visible, blank], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    _patch_instant_sleep(mocker)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert len(actions.tap.call_args_list) == 1  # one click after retry succeeded


@pytest.mark.asyncio
async def test_player_bound_while_match_uses_implicit_search_region(
    tmp_path: Path,
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """`while_match: x` should search inside implicit `x_search` when present."""
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[70:80, 70:80] = _claim_pattern()
    _write_player_bound_scenario(tmp_path, frame)
    (tmp_path / "area.json").write_text(
        yaml.dump(
            {
                "screens": [
                    {
                        "id": 1,
                        "ocr": "references/page.worker.add.png",
                        "regions": [
                            {
                                "name": "page.worker.add",
                                "bbox": {"x": 20, "y": 20, "width": 10, "height": 10},
                                "threshold": 0.9,
                            },
                            {
                                "name": "page.worker.add_search",
                                "bbox": {"x": 60, "y": 60, "width": 30, "height": 30},
                                "overlay_auxiliary": True,
                            },
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    # Template is still exported from the primary bbox in the reference image.
    ref = np.zeros((100, 100, 3), dtype=np.uint8)
    ref[20:30, 20:30] = _claim_pattern()
    cv2.imwrite(
        str(tmp_path / "references/crop/page.worker.add_page.worker.add.png"),
        ref[20:30, 20:30],
    )

    actions = make_actions([frame, np.zeros((100, 100, 3), dtype=np.uint8)], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    _patch_instant_sleep(mocker)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    tap_call = actions.tap.call_args_list[0]
    assert tap_call[0][0] == "bs1"
    assert tap_call[0][1].x == 75
    assert tap_call[0][1].y == 75
    assert tap_call[1]["approval_region"] == "page.worker.add"
    row = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]
    assert row["dsl_last_match_search_region"] == "page.worker.add_search"


@pytest.mark.asyncio
async def test_assign_worker_while_match_real_fixture_matches_search_roi(
    mocker,
    redis_async: object,
) -> None:
    """Real PNG fixture matches ``page.worker.add`` (sliding search); Redis carries search_region."""
    repo_root = Path(__file__).resolve().parents[2]
    fixture = repo_root / "tests" / "fixtures" / "page_worker_add_current_state.png"
    frame = cv2.imread(str(fixture))
    assert frame is not None, f"fixture missing or unreadable: {fixture}"
    blank = np.zeros_like(frame)
    actions = make_actions([frame, blank])
    patch_dsl(mocker, actions, repo_root=repo_root)
    mocker.patch.object(dsl, "click_approval_enabled", return_value=False)
    _patch_instant_sleep(mocker)
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"current_screen": "survivor_status"},
    )

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="assign_worker",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list
    md = result.metadata or {}
    assert md.get("scenario_completed") is True
    row = await redis_async.hgetall("wos:instance:bs1:state")  # type: ignore[attr-defined]
    assert row["dsl_last_match_search_region"] == "page.worker.add_search"


@pytest.mark.asyncio
async def test_player_bound_while_match_honors_explicit_retry_block(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """`retry: {attempts, interval_seconds}` overrides the player-bound defaults."""
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, _frame_with_pattern())
    yaml_path = _scenario_root(tmp_path) / "workers" / "test_assign.yaml"
    raw = yaml.safe_load(yaml_path.read_text())
    # Use the duration-string form ("250ms") to also exercise the parser.
    raw["steps"][0]["retry"] = {"attempts": 3, "interval": "250ms"}
    yaml_path.write_text(yaml.dump(raw), encoding="utf-8")

    actions = make_actions([blank, blank, blank, blank, blank], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    mocker.patch.object(dsl, "click_approval_enabled", return_value=False)
    _patch_instant_sleep(mocker)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is False
    md = result.metadata or {}
    assert md.get("attempts") == 3            # honored override
    assert md.get("interval") == 0.25         # "250ms" parsed to seconds
    # Three probe attempts, none matched → exactly 3 captures.
    assert actions.capture_screen_bgr.call_count == 3


@pytest.mark.asyncio
async def test_device_level_while_match_zero_iterations_returns_success(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Device-level scenarios keep legacy 0-iteration silent success."""
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    _write_player_bound_scenario(tmp_path, _frame_with_pattern())
    # Mark the scenario as device_level so legacy semantics apply.  Also drop
    # the helper's ``strict: True`` — strict mode runs the approval-pause path
    # which blocks waiting for a UI response (redis-backed) and would hang.
    yaml_path = _scenario_root(tmp_path) / "workers" / "test_assign.yaml"
    raw = yaml.safe_load(yaml_path.read_text())
    raw["device_level"] = True
    for s in raw.get("steps", []):
        s.pop("strict", None)
    yaml_path.write_text(yaml.dump(raw), encoding="utf-8")

    actions = make_actions([blank, blank], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)
    _patch_instant_sleep(mocker)

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="test_assign",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert result.next_run_at is None  # no reschedule
    assert actions.tap.call_args_list == []
    # Only one probe attempt (default for device_level).
    assert actions.capture_screen_bgr.call_count == 1


def _frame_with_pattern() -> np.ndarray:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    frame[20:30, 20:30] = _claim_pattern()
    return frame


def _write_red_dot_guard_scenario(tmp_path: Path) -> None:
    """A player-bound scenario whose only step is an ``isRedDot:`` guard.

    The point: zero matches must NOT trigger the strict-mode approval pause
    (``while_match matched zero times``). Red-dot guards are by design
    "tap iff the indicator is lit"; the off-state is the common case and
    must skip silently so subsequent steps run.
    """
    scenario_root = _scenario_root(tmp_path)
    (scenario_root / "guarded.yaml").write_text(
        yaml.dump(
            {
                "enabled": True,
                "name": "guarded",
                "steps": [
                    {
                        "while_match": "page.vip.box",
                        "isRedDot": True,
                        "max": 1,
                        "steps": [{"click": "page.vip.box"}],
                    },
                    # Marker step proving the scenario continued past the
                    # guard rather than pausing for approval.
                    {"exec": "marker"},
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
                        "screen_id": "vip",
                        "regions": [
                            {
                                "name": "page.vip.box",
                                "action": "exist",
                                "bbox": {
                                    "x": 10.0,
                                    "y": 10.0,
                                    "width": 5.0,
                                    "height": 5.0,
                                },
                                "has_red_dot": True,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_red_dot_guard_with_zero_matches_skips_silently_not_strict(
    tmp_path: Path,
    mocker,
    redis_async: object,
) -> None:
    """Regression: ``while_match: <reg> isRedDot:true max:1`` with no red-dot
    on screen used to pop the "while_match matched zero times" approval
    prompt in player-bound scenarios, blocking the operator from getting
    past the first guard. The strict default is now disabled for state-
    check guards so the scenario continues to the next step normally."""
    _write_red_dot_guard_scenario(tmp_path)
    # Blank frame → red dot detector finds nothing → guard matches 0 times.
    blank = np.zeros((100, 100, 3), dtype=np.uint8)
    actions = make_actions([blank, blank, blank], resolution=(100, 100))
    patch_dsl(mocker, actions, repo_root=tmp_path)

    marker_fired = {"n": 0}

    async def _marker(_ctx: Any) -> None:
        marker_fired["n"] += 1

    import tasks.dsl_exec as dsl_exec

    mocker.patch.dict(dsl_exec.DSL_EXEC_REGISTRY, {"marker": _marker})

    task = dsl.DslScenarioTask(
        task_id="t1",
        player_id="p1",
        scenario_key="guarded",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    # Scenario reached the next step — strict pause did NOT fire.
    assert marker_fired["n"] == 1
    # No tap happened (red-dot not present).
    assert actions.tap.call_args_list == []
