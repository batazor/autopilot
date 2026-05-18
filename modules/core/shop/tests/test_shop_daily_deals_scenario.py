"""Behavioral tests for the ``shop.daily_deals`` scenario.

The scenario is a single ``while_match: box.free`` loop with ``max: 1`` —
when the free chest is visible on the Daily Deals page, tap it once and
exit. Two execution paths matter:

* **Claim path** — start frame shows the ``Free`` chest on the page, the
  scenario taps once. ``max: 1`` then drops out of the loop without a
  second probe.
* **No-op path** — start frame has the chest area painted out so the
  template never matches. The scenario exits without tapping. This guards
  against accidental "tap if region exists at all" regressions; the action
  is supposed to be template-match-gated only.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import ANY, call

import cv2
import pytest
from conftest import make_actions, patch_dsl

import tasks.dsl_scenario as dsl
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"

FREE_BOX_REGION = "box.free"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


def _free_box_bbox() -> dict[str, float]:
    """Pull the live bbox out of the merged area doc.

    Hard-coding would silently drift if the annotator resizes the region;
    reading from area.yaml keeps the test self-healing.
    """
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == FREE_BOX_REGION:
                return region["bbox"]
    pytest.fail(f"region {FREE_BOX_REGION!r} not in area doc")


def _wipe_template(frame: np.ndarray, bbox: dict[str, float]) -> None:
    """Paint over ``bbox`` with mid-gray so template match falls below 0.9.

    The scenario relies on plain template matching against the cropped
    ``box.free`` PNG; replacing the chest pixels with a flat grey patch
    drops normalized cross-correlation well under the 0.9 threshold.
    """
    h, w = frame.shape[:2]
    x0 = int(bbox["x"] / 100 * w)
    x1 = int((bbox["x"] + bbox["width"]) / 100 * w)
    y0 = int(bbox["y"] / 100 * h)
    y1 = int((bbox["y"] + bbox["height"]) / 100 * h)
    frame[y0:y1, x0:x1] = (80, 80, 80)


@pytest.fixture
def free_box_bbox() -> dict[str, float]:
    return _free_box_bbox()


# ── Scenario structural sanity ───────────────────────────────────────────────


def test_shop_daily_deals_scenario_file_exists() -> None:
    """Guards against accidental deletion / rename of the scenario file."""
    assert (MODULE_DIR / "scenarios" / "shop.daily_deals.yaml").exists()


# ── Execution paths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_clicks_free_chest_when_visible(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
) -> None:
    """``Free`` chest visible on the page → tap once, then ``max: 1`` exits."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop.daily_deals"},
    )

    frame = _load_bgr("page.shop.daily_deals.png")

    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-daily-deals-claim",
        player_id="p1",
        scenario_key="shop.daily_deals",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region=FREE_BOX_REGION),
    ]


@pytest.mark.asyncio
async def test_no_op_when_free_chest_absent(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
    free_box_bbox: dict[str, float],
) -> None:
    """No matching ``Free`` chest template on entry → scenario exits without tapping."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop.daily_deals"},
    )

    frame = _load_bgr("page.shop.daily_deals.png")
    _wipe_template(frame, free_box_bbox)

    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-daily-deals-noop",
        player_id="p1",
        scenario_key="shop.daily_deals",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == []
