from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from conftest import patch_dsl_bot_actions

import tasks.dsl_scenario as dsl
from navigation.detector import ScreenDetector
from scenarios import template_resolver
from services import get_ocr_client

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"


class _FakeActions:
    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = frames
        self.capture_count = 0
        self.tapped: list[tuple[str, int, int, str | None]] = []

    def screen_resolution(self, instance_id: str) -> tuple[int, int]:
        assert instance_id == "bs1"
        return 720, 1280

    def capture_screen_bgr(self, instance_id: str) -> np.ndarray:
        assert instance_id == "bs1"
        idx = min(self.capture_count, len(self.frames) - 1)
        self.capture_count += 1
        return self.frames[idx]

    def tap(self, instance_id: str, point: Any, *, approval_region: str | None = None) -> bool:
        self.tapped.append((instance_id, point.x, point.y, approval_region))
        return True


def _load_reference_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def test_welcome_new_survivors_scenario_is_registered_with_expected_shape() -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, "welcome_new_survivors")
    assert loaded is not None

    path, doc = loaded
    assert path == MODULE_DIR / "scenarios" / "welcome_new_survivors.yaml"
    assert doc["enabled"] is True
    assert doc["node"] == "isNewPeople"
    assert doc["priority"] == 100_000

    steps = doc["steps"]
    assert [step["while_match"] for step in steps] == ["button.welcome_in"]
    assert steps[0]["max"] == 1
    assert steps[0]["steps"] == [
        {"click": "button.welcome_in"},
        {"wait": "2s"},
    ]


@pytest.mark.asyncio
async def test_welcome_new_survivors_rehearses_main_city_to_welcome_in(
    monkeypatch: pytest.MonkeyPatch,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    main_city = _load_reference_bgr("welcome_new_survivors.rehearsal.01.main_city.png")
    welcome_in = _load_reference_bgr("welcome_new_survivors.rehearsal.02.welcome_in.png")
    after_welcome = _load_reference_bgr("welcome_new_survivors.rehearsal.03.after_welcome_in.png")

    detector = ScreenDetector(get_ocr_client())
    assert await detector.detect_screen(main_city) == "main_city"
    assert await detector.detect_screen(welcome_in) == "isNewPeople"
    assert await detector.detect_screen(after_welcome) == "main_city"

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "main_city"},
    )

    actions = _FakeActions(
        [
            main_city,     # Navigator detects current node.
            welcome_in,    # Navigator verifies after tapping `isNewPeople`.
            welcome_in,    # Navigator may re-check during route verification.
            welcome_in,    # `while_match: button.welcome_in`.
            after_welcome,  # Screen returns to main_city after clicking `button.welcome_in`.
        ]
    )
    monkeypatch.setattr(dsl, "_repo_root", lambda: REPO_ROOT)
    patch_dsl_bot_actions(monkeypatch, actions)

    task = dsl.DslScenarioTask(
        task_id="welcome-new-survivors-rehearsal",
        player_id="p1",
        scenario_key="welcome_new_survivors",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tapped == [
        ("bs1", 107, 278, "isNewPeople"),
        ("bs1", 356, 1072, "button.welcome_in"),
    ]
