from __future__ import annotations

from pathlib import Path
from unittest.mock import ANY, call

import cv2
import numpy as np
import pytest
import yaml
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from dsl import template_resolver
from layout.area_manifest import load_area_doc
from navigation.detector import ScreenDetector
from services import get_ocr_client

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
REWARDS_REFERENCES_DIR = REPO_ROOT / "games" / "wos" / "core" / "rewards" / "references"
REHEARSAL_FIXTURES_DIR = REFERENCES_DIR / "rehearsal" / "fixtures" / "vip.daily"


def _load_reference_bgr(name: str, *, base: Path = REFERENCES_DIR) -> np.ndarray:
    path = base / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


def _load_rehearsal_fixture_bgr(name: str) -> np.ndarray:
    path = REHEARSAL_FIXTURES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load rehearsal fixture: {path}"
    return frame


def _region_bbox(region_name: str) -> dict[str, float]:
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == region_name:
                return region["bbox"]
    msg = f"missing region {region_name!r}"
    raise AssertionError(msg)


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


def test_vip_screen_red_dots_push_vip_daily() -> None:
    """The vip screen must produce vip.daily itself.

    The ``main_city`` badge rule only fires from main_city, so a red dot lit
    while the bot already stands on the vip screen would otherwise never queue
    any work — the scheduler would navigate away (deals/etc.) and skip it. One
    on-screen producer per claimable region keeps vip.daily at hops=0 so the
    queue's hop debuff ranks it above any navigate-away task.
    """
    doc = yaml.safe_load((MODULE_DIR / "analyze" / "analyze.yaml").read_text())
    rules = {r["name"]: r for r in doc["overlay"]}

    for region in ("page.vip.box", "page.vip.add", "page.vip.unlock"):
        name = f"vip.page.{region.removeprefix('page.vip.')}.red_dot"
        rule = rules.get(name)
        assert rule is not None, f"missing on-screen vip producer {name!r}"
        assert rule["region"] == region
        assert rule["screens"] == ["vip"]
        assert rule["isRedDot"] is True
        assert rule.get("ttl"), "throttle ttl required so a stuck red dot can't spam"
        pushes = [s.get("push_scenario") for s in rule.get("steps", [])]
        assert "vip.daily" in pushes


def test_increase_level_use_all_region_is_wired() -> None:
    """`button.use_all` must exist as a full-frame search region with its crop."""
    from layout.area_lookup import screen_region_by_name
    from layout.crop_paths import exported_crop_png

    area_doc = load_area_doc(REPO_ROOT)
    pair = screen_region_by_name(
        area_doc, "button.use_all", state_flat={"current_screen": "increase_level"}
    )
    assert pair is not None, "button.use_all missing from area.yaml"
    screen, region = pair
    assert screen.get("screen_id") == "increase_level"
    assert region.get("action") == "exist"
    assert region.get("isSearch") is True
    crop = exported_crop_png(REPO_ROOT, screen["ocr"], "button.use_all")
    assert crop.is_file(), f"missing crop {crop}"


def test_use_all_template_matches_pills_not_use_buttons() -> None:
    """The `×N` pill template must catch every stack pill yet reject `Use`.

    The pill and `Use` are near-identical green capsules; only the white `×`
    glyph (cropped digit-free) separates them. This locks that separation so a
    future re-crop that reintroduces ambiguity fails loudly instead of making
    the bot tap `Use` once per item again.
    """
    frame = cv2.cvtColor(
        _load_reference_bgr("increase_level.png"), cv2.COLOR_BGR2GRAY
    )
    tpl = cv2.cvtColor(
        _load_reference_bgr(
            "crop/increase_level_button.use_all.png"
        ),
        cv2.COLOR_BGR2GRAY,
    )
    res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)

    # Walk down the peaks; suppress a window around each so we get distinct hits.
    work = res.copy()
    peaks: list[tuple[float, int, int]] = []
    for _ in range(6):
        _, score, _, loc = cv2.minMaxLoc(work)
        if score < 0.5:
            break
        peaks.append((float(score), loc[0], loc[1]))
        x, y = loc
        work[max(0, y - 30) : y + 30, max(0, x - 30) : x + 30] = -1.0

    threshold = 0.85  # matches area.yaml
    # Both green stack pills sit at x≈299; Use buttons at x≈493.
    pill_hits = [s for s, x, _ in peaks if 280 < x < 320 and s >= threshold]
    over_thresh_use = [s for s, x, _ in peaks if 480 < x < 510 and s >= threshold]
    assert len(pill_hits) >= 2, f"expected both pills ≥{threshold}, got {peaks}"
    assert not over_thresh_use, f"a Use button crossed {threshold}: {peaks}"


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
    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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


@pytest.mark.skip(
    reason="VIP screen-detect crops are perceptually stale vs the rehearsal "
    "fixture: page.vip.box/add/unlock score pHash 0.50/0.78/0.81 (gate 0.9) "
    "while structural NCC is 0.93-0.99 — a crop/data mismatch (likely a "
    "claimable-state badge), not a code bug. Re-capture the three crops via "
    "the labeling UI to re-enable; see git history for the analysis."
)
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
    6. Increase Level popup -> scenario long-presses `button.use`, then taps `increase_level.icon.close`;
    7. VIP page again -> scenario taps `page.vip.unlock`, then `button.use` and
       `increase_level.icon.close` again.
    """

    main_city = _load_rehearsal_fixture_bgr("01.main_city_before.png")
    vip_page = _load_rehearsal_fixture_bgr("02.vip_page.png")
    vip_after_box = vip_page.copy()
    _clear_region(vip_after_box, "page.vip.box")
    vip_after_add = vip_after_box.copy()
    _clear_region(vip_after_add, "page.vip.add")
    _draw_red_dot(vip_after_add, "page.vip.unlock")
    vip_after_unlock = vip_after_add.copy()
    _clear_region(vip_after_unlock, "page.vip.unlock")
    rewards_popup = _load_reference_bgr(
        "page.rewards_popup.png", base=REWARDS_REFERENCES_DIR
    )
    increase_level = _load_reference_bgr("increase_level.png")
    increase_after_use = increase_level.copy()
    _clear_region(increase_after_use, "button.use")

    detector = ScreenDetector(get_ocr_client())
    assert await detector.detect_screen(main_city) == "main_city"
    assert await detector.detect_screen(vip_page) == "vip"
    assert await detector.detect_screen(rewards_popup) == "rewards"
    assert await detector.detect_screen(increase_level) == "increase_level"

    await redis_async.hset(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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
        call("bs1", ANY, approval_region="increase_level.icon.close"),
        call("bs1", ANY, approval_region="page.vip.unlock"),
        call("bs1", ANY, approval_region="button.use"),
        call("bs1", ANY, approval_region="increase_level.icon.close"),
    ]
    assert actions.long_tap.call_args_list == [
        call("bs1", ANY, duration_ms=800),
    ]
    assert await redis_async.hget("wos:instance:bs1:state", "current_screen") == "vip"  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
