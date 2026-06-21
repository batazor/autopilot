"""Behavioral tests for the ``shop.dawn_market`` scenario.

The scenario is a single ``while_match: page.shop.dawn_market.claimable`` loop
gated by ``isRedDot: true`` — as long as the chest claimable region carries a
red dot, tap it and wait. Two execution paths matter:

* **Claim path** — start frame shows the badge, scenario taps once, next
  capture has the badge cleared → loop exits cleanly.
* **No-op path** — start frame has the badge already cleared, scenario
  exits without tapping. This guards against accidental "tap if region is
  visible" regressions; the action is supposed to be red-dot-gated only.
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
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"

CLAIMABLE_REGION = "page.shop.dawn_market.claimable"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


def _claimable_bbox() -> dict[str, float]:
    """Read the live bbox out of the merged area doc.

    Hard-coding would silently drift if the annotator resizes the region;
    pulling from area.yaml keeps the test self-healing.
    """
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == CLAIMABLE_REGION:
                return region["bbox"]
    pytest.fail(f"region {CLAIMABLE_REGION!r} not in area doc")


def _wipe_red_dot(frame: np.ndarray, bbox: dict[str, float]) -> None:
    """Overwrite ``bbox`` (and surrounding padding) with mid-gray so the
    red-dot detector never matches.

    ``has_red_dot_in_bbox_percent`` extends the search ROI well above the
    labeled bbox (to catch unread-count badges that overflow upward) and
    pads sideways too. Painting only the labeled bbox leaves badges in the
    fallback region intact, so the loop never exits. 100 % padding on each
    side covers the engine's edge_badge fallback (~85 % patch_h upward
    extension) with comfortable headroom.
    """
    h, w = frame.shape[:2]
    bw = int(bbox["width"] / 100 * w)
    bh = int(bbox["height"] / 100 * h)
    cx = int((bbox["x"] + bbox["width"] / 2) / 100 * w)
    cy = int((bbox["y"] + bbox["height"] / 2) / 100 * h)
    frame[
        max(0, cy - bh) : min(h, cy + bh),
        max(0, cx - bw) : min(w, cx + bw),
    ] = (80, 80, 80)


@pytest.fixture
def claimable_bbox() -> dict[str, float]:
    return _claimable_bbox()


# ── Scenario structural sanity ───────────────────────────────────────────────


def test_shop_dawn_market_scenario_file_exists() -> None:
    """Guards against accidental deletion / rename of the scenario file."""
    assert (MODULE_DIR / "scenarios" / "shop.dawn_market.yaml").exists()


# ── Execution paths ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claims_chest_when_red_dot_present(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
    claimable_bbox: dict[str, float],
) -> None:
    """Red dot present → tap claimable once, then the cleared frame ends the loop."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop.dawn_market"},
    )

    frame_with_dot = _load_bgr("page.shop.dawn_market.png")
    frame_cleared = frame_with_dot.copy()
    _wipe_red_dot(frame_cleared, claimable_bbox)

    actions = make_actions([frame_with_dot, frame_cleared])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-dawn-market-claim",
        player_id="p1",
        scenario_key="shop.dawn_market",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == [
        call("bs1", ANY, approval_region=CLAIMABLE_REGION),
    ]


@pytest.mark.asyncio
async def test_no_op_when_no_red_dot(
    mocker,
    redis_async: object,
    pin_click_to_center: None,
    claimable_bbox: dict[str, float],
) -> None:
    """No red dot on entry → scenario exits without tapping."""
    await redis_async.hset(  # type: ignore[attr-defined]
        "wos:instance:bs1:state",
        mapping={"active_player": "p1", "current_screen": "shop.dawn_market"},
    )

    frame = _load_bgr("page.shop.dawn_market.png")
    _wipe_red_dot(frame, claimable_bbox)

    actions = make_actions([frame])
    patch_dsl(mocker, actions, repo_root=REPO_ROOT)

    task = dsl.DslScenarioTask(
        task_id="shop-dawn-market-noop",
        player_id="p1",
        scenario_key="shop.dawn_market",
        redis_client=redis_async,  # type: ignore[arg-type]
    )
    result = await task.execute("bs1")

    assert result.success is True
    assert actions.tap.call_args_list == []
