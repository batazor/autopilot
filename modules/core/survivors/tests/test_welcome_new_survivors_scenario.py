from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from analysis.overlay_engine import evaluate_overlay_rules_async
from dsl import template_resolver
from layout.area_manifest import load_area_doc
from navigation.detector import ScreenDetector
from services import get_ocr_client

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
REHEARSAL_FIXTURES_DIR = (
    REFERENCES_DIR / "rehearsal" / "fixtures" / "welcome_new_survivors"
)


def _load_reference_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def _load_rehearsal_fixture_bgr(name: str) -> np.ndarray:
    path = REHEARSAL_FIXTURES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load rehearsal fixture: {path}"
    return frame


def test_welcome_new_survivors_scenario_is_registered_with_expected_shape(snapshot) -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, "welcome_new_survivors")
    assert loaded is not None

    path, doc = loaded
    assert path == MODULE_DIR / "scenarios" / "welcome_new_survivors.yaml"
    assert doc == snapshot


@pytest.mark.asyncio
async def test_welcome_new_survivors_rehearses_main_city_to_welcome_in(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    main_city = _load_rehearsal_fixture_bgr("01.main_city_before.png")
    welcome_in = _load_rehearsal_fixture_bgr("02.welcome_in.png")
    after_welcome = _load_rehearsal_fixture_bgr("03.after_welcome_in.png")

    detector = ScreenDetector(get_ocr_client())
    assert await detector.detect_screen(main_city) == "main_city"
    assert await detector.detect_screen(welcome_in) == "isNewPeople"
    assert await detector.detect_screen(after_welcome) == "main_city"

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "main_city"},
    )

    actions = make_actions(
        [
            main_city,     # Navigator detects current node.
            welcome_in,    # Navigator verifies after tapping `isNewPeople`.
            welcome_in,    # Navigator may re-check during route verification.
            welcome_in,    # `while_match: button.welcome_in`.
            after_welcome,  # Screen returns to main_city after clicking `button.welcome_in`.
        ]
    )
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="welcome-new-survivors-rehearsal",
        player_id="p1",
        scenario_key="welcome_new_survivors",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call(
            "bs1",
            ANY,
            approval_region="isNewPeople",
            approval_source="navigation",
            approval_context=ANY,
        ),
        call("bs1", ANY, approval_region="button.welcome_in"),
    ]


@pytest.mark.asyncio
async def test_isworkers_red_dot_detected_after_welcome_in() -> None:
    frame = _load_reference_bgr("page.main_city.after_welcome.png")

    out = await evaluate_overlay_rules_async(
        frame,
        load_area_doc(REPO_ROOT),
        REPO_ROOT,
        [
            {
                "name": "isWorkers.visible",
                "region": "isWorkers",
                "isRedDot": True,
            },
        ],
        current_screen="main_city",
    )

    row = out["isWorkers.visible"]
    assert row["matched"] is True
    assert row["action"] == "red_dot"
    assert row["red_dot_present"] is True


@pytest.mark.asyncio
async def test_survivor_status_status_tab_detects_active_tab_and_add_button() -> None:
    frame = _load_reference_bgr("page.worker.png")

    detector = ScreenDetector(get_ocr_client())
    assert await detector.detect_screen(frame) == "survivor_status.status"

    out = await evaluate_overlay_rules_async(
        frame,
        load_area_doc(REPO_ROOT),
        REPO_ROOT,
        [
            {
                "name": "survivor_status.status.active",
                "region": "survivor_status.status",
                "isTabActive": True,
            },
            {
                "name": "survivor_status.details.active",
                "region": "survivor_status.details",
                "isTabActive": True,
            },
            {
                "name": "button.add.visible",
                "region": "button.add",
                "action": "findIcon",
                "threshold": 0.9,
            },
        ],
        current_screen="survivor_status.status",
    )

    status_tab = out["survivor_status.status.active"]
    details_tab = out["survivor_status.details.active"]
    add_button = out["button.add.visible"]

    assert status_tab["matched"] is True
    assert status_tab["tab_active"] is True
    assert details_tab["matched"] is False
    assert details_tab["tab_active"] is False
    assert add_button["matched"] is True
