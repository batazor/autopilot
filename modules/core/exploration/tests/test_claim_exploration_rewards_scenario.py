from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, call

import cv2
import numpy as np
import pytest
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from navigation.detector import ScreenDetector
from scenarios import template_resolver
from services import get_ocr_client

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"


def _load_reference_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def test_claim_exploration_rewards_scenario_is_registered_with_expected_shape() -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, "claim_exploration_rewards")
    assert loaded is not None

    path, doc = loaded

    assert path == MODULE_DIR / "scenarios" / "by_cron" / "claim_exploration_rewards.yaml"
    assert doc["enabled"] is True
    assert doc["node"] == "exploration"
    assert [next(iter(step)) for step in doc["steps"]] == ["click", "wait", "while_match"]


@pytest.mark.asyncio
async def test_claim_exploration_rewards_rehearses_main_city_reward_flow(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    main_city = _load_reference_bgr(
        "claim_exploration_rewards.rehearsal.01.main_city_before.png"
    )
    exploration = _load_reference_bgr("claim_exploration_rewards.rehearsal.03.state_03.png")
    idle_income = _load_reference_bgr("claim_exploration_rewards.rehearsal.08.state_08.png")
    rewards = _load_reference_bgr("claim_exploration_rewards.rehearsal.11.state_11.png")
    after_rewards = _load_reference_bgr("claim_exploration_rewards.rehearsal.14.state_14.png")

    detector = ScreenDetector(get_ocr_client())
    assert await detector.detect_screen(main_city) == "main_city"
    assert await detector.detect_screen(exploration) == "exploration"
    assert await detector.detect_screen(rewards) == "rewards"
    assert await detector.detect_screen(after_rewards) == "exploration"

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "main_city"},
    )

    actions = make_actions(
        [
            main_city,      # Navigator detects current node.
            exploration,    # Navigator verifies after tapping `main_city.to.exploration`.
            exploration,    # Navigator may re-check during route verification.
            exploration,    # Step 0: `click: button.claim`.
            idle_income,    # Step 2: `while_match: button.claim.big`.
            rewards,        # Step 2.0.2: `match: button.tap_anywhere_to_exit`.
            rewards,        # Step 2.0.2.steps.0: click tap-anywhere.
            after_rewards,  # Next `button.claim.big` probe exits the loop.
            after_rewards,
        ]
    )
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="claim-exploration-rewards-rehearsal",
        player_id="p1",
        scenario_key="claim_exploration_rewards",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call(
            "bs1",
            ANY,
            approval_region="main_city.to.exploration",
            approval_source="navigation",
            approval_context=ANY,
        ),
        call("bs1", ANY, approval_region="button.claim"),
        call("bs1", ANY, approval_region="button.claim.big"),
        call("bs1", ANY, approval_region="button.tap_anywhere_to_exit"),
    ]
