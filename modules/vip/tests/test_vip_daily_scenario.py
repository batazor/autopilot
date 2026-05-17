from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, call

import cv2
import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from navigation.detector import ScreenDetector
from scenarios import template_resolver
from services import get_ocr_client

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[1]
REFERENCES_DIR = MODULE_DIR / "references"


def _load_reference_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def _region_bbox(region_name: str) -> dict[str, float]:
    area_doc = yaml.safe_load((REPO_ROOT / "area.json").read_text(encoding="utf-8"))
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == region_name:
                return region["bbox"]
    raise AssertionError(f"missing region {region_name!r}")


def _draw_red_dot(frame: np.ndarray, region_name: str) -> None:
    bbox = _region_bbox(region_name)
    width = frame.shape[1]
    height = frame.shape[0]
    x0 = int(width * float(bbox["x"]) / 100)
    y0 = int(height * float(bbox["y"]) / 100)
    w = int(width * float(bbox["width"]) / 100)
    h = int(height * float(bbox["height"]) / 100)
    cv2.rectangle(frame, (x0, y0), (x0 + w, y0 + h), (255, 128, 0), -1)
    center = (x0 + max(6, w // 2), y0 + max(6, h // 2))
    radius = 10
    cv2.circle(frame, center, radius, (0, 0, 255), -1)
    cv2.circle(frame, center, max(3, radius // 3), (255, 255, 255), -1)


def _clear_region(frame: np.ndarray, region_name: str) -> None:
    bbox = _region_bbox(region_name)
    width = frame.shape[1]
    height = frame.shape[0]
    x0 = int(width * float(bbox["x"]) / 100)
    y0 = int(height * float(bbox["y"]) / 100)
    w = int(width * float(bbox["width"]) / 100)
    h = int(height * float(bbox["height"]) / 100)
    cv2.rectangle(frame, (x0, y0), (x0 + w, y0 + h), (80, 80, 80), -1)


def test_vip_daily_scenario_is_registered_with_expected_shape(snapshot) -> None:
    loaded = template_resolver.load_doc(REPO_ROOT, "vip.daily")
    assert loaded is not None

    path, doc = loaded
    assert path == MODULE_DIR / "scenarios" / "by_cron" / "vip.daily.yaml"
    assert doc == snapshot


@pytest.mark.asyncio
async def test_vip_daily_scenario_clicks_claimable_vip_box(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "vip"},
    )

    visible = np.zeros((1280, 720, 3), dtype=np.uint8)
    _draw_red_dot(visible, "page.vip.box")
    blank = np.zeros((1280, 720, 3), dtype=np.uint8)

    actions = make_actions([visible, blank])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="vip-daily-test",
        player_id="p1",
        scenario_key="vip.daily",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [call("bs1", ANY, approval_region="page.vip.box")]


@pytest.mark.asyncio
async def test_vip_daily_scenario_rehearses_main_city_to_vip_reward_popup(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """Replay real rehearsal frames as the bot's screen source.

    Frame flow:
    1. main_city with VIP badge visible -> Navigator taps `page.vip`;
    2. VIP page with daily box red dot -> scenario taps `page.vip.box`;
    3. Rewards popup -> scenario taps `button.click_to_continue`;
    4. VIP page again -> scenario probes optional `button.claim`;
    5. VIP page again -> scenario taps `page.vip.add`;
    6. Increase Level popup -> scenario taps `button.use`.
    7. VIP page again -> scenario taps `page.vip.unlock` with the same popup flow.
    """

    main_city = _load_reference_bgr("mcp.vip.rehearsal.08.start.png")
    vip_page = _load_reference_bgr("mcp.vip.rehearsal.09.after_vip_tap.png")
    vip_after_box = vip_page.copy()
    _clear_region(vip_after_box, "page.vip.box")
    vip_after_add = vip_after_box.copy()
    _clear_region(vip_after_add, "page.vip.add")
    _draw_red_dot(vip_after_add, "page.vip.unlock")
    vip_after_unlock = vip_after_add.copy()
    _clear_region(vip_after_unlock, "page.vip.unlock")
    rewards_popup = _load_reference_bgr("mcp.vip.rehearsal.10.after_box.png")
    increase_level = _load_reference_bgr("page.increase_level.png")
    increase_after_use = increase_level.copy()
    _clear_region(increase_after_use, "button.use")

    detector = ScreenDetector(get_ocr_client())
    assert await detector.detect_screen(main_city) == "main_city"
    assert await detector.detect_screen(vip_page) == "vip"
    assert await detector.detect_screen(rewards_popup) == "rewards"
    assert await detector.detect_screen(increase_level) == "increase_level"

    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "main_city"},
    )

    actions = make_actions(
        [
            main_city,      # Navigator detects current node.
            vip_page,       # Navigator verifies the page after tapping `page.vip`.
            vip_page,       # Navigator may re-check during route verification.
            vip_page,       # `while_match: page.vip.box`.
            rewards_popup,  # `while_match: button.click_to_continue`.
            vip_after_box,  # Box red dot is gone after `button.click_to_continue`.
            vip_after_box,  # `while_match: button.claim` retry miss 1.
            vip_after_box,  # `while_match: button.claim` retry miss 2.
            vip_after_box,  # `while_match: button.claim` retry miss 3.
            vip_after_box,  # `while_match: page.vip.add`.
            increase_level,  # `while_match: button.use` after tapping `page.vip.add`.
            increase_after_use,  # `while_match: button.use` exits after use.
            increase_after_use,  # `while_match: increase_level.icon.close`.
            vip_after_add,  # Add red dot is gone after closing the add popup.
            vip_after_add,  # `while_match: page.vip.unlock`.
            vip_after_add,  # Unlock guard probes after the add loop settles.
            increase_level,  # `while_match: button.use` after tapping unlock.
            increase_after_use,  # `while_match: button.use` exits after use.
            increase_after_use,  # `while_match: increase_level.icon.close`.
            vip_after_unlock,  # Unlock red dot is gone after closing its popup.
        ]
    )
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="vip-daily-real-frame-rehearsal",
        player_id="p1",
        scenario_key="vip.daily",
        redis_client=redis_async,  # type: ignore[arg-type]
    )

    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call(
            "bs1",
            ANY,
            approval_region="page.vip",
            approval_source="navigation",
            approval_context=ANY,
        ),
        call("bs1", ANY, approval_region="page.vip.box"),
        call("bs1", ANY, approval_region="button.click_to_continue"),
        call("bs1", ANY, approval_region="page.vip.add"),
        call("bs1", ANY, approval_region="button.use"),
        call("bs1", ANY, approval_region="page.vip.unlock"),
        call("bs1", ANY, approval_region="button.use"),
    ]
    assert await redis_async.hget("wos:instance:bs1:state", "current_screen") == "vip"  # type: ignore[attr-defined]
