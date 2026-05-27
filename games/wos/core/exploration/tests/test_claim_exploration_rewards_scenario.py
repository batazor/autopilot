from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from dsl import template_resolver
from navigation.detector import ScreenDetector
from services import get_ocr_client

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"
REWARDS_REFERENCES_DIR = REPO_ROOT / "modules" / "core" / "rewards" / "references"
REHEARSAL_FIXTURES_DIR = REFERENCES_DIR / "rehearsal" / "fixtures" / "claim_exploration_rewards"


def _load_reference_bgr(name: str, *, base: Path = REFERENCES_DIR) -> np.ndarray:
    path = base / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def _load_rehearsal_fixture_bgr(name: str) -> np.ndarray:
    path = REHEARSAL_FIXTURES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def test_claim_exploration_rewards_scenario_is_registered_with_expected_shape(snapshot) -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, "claim_exploration_rewards")
    assert loaded is not None

    path, doc = loaded
    assert path == MODULE_DIR / "scenarios" / "by_cron" / "claim_exploration_rewards.yaml"
    assert doc == snapshot


@pytest.mark.asyncio
async def test_claim_exploration_rewards_rehearses_main_city_reward_flow(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    main_city = _load_rehearsal_fixture_bgr("01.main_city_before.png")
    exploration = _load_rehearsal_fixture_bgr("03.exploration.png")
    idle_income = _load_rehearsal_fixture_bgr("08.idle_income.png")
    rewards = _load_reference_bgr("page.rewards.png", base=REWARDS_REFERENCES_DIR)
    after_rewards = _load_rehearsal_fixture_bgr("14.after_rewards.png")

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
