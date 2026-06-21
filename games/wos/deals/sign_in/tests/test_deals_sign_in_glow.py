"""Detect the sign-in page landmark + which day reward is claimable.

The sign-in page shows a 7-day reward timeline in three columns
(``Free`` | ``Day N`` | ``Epic``). The next claimable tile in the Free
column gets a saturated yellow-to-orange rim around it; locked rows show
flat colors. Same pattern as ``modules/core/shop/scenarios/shop.dawn_fund``
— the ``deals.sign_in`` scenario uses ``while_match: deals.sign_in.free``
with ``isYellowGlow: true`` to enumerate and click each claimable tile.

Contracts covered:

* ``deals.sign_in.title`` is detected via ``findIcon`` on the reference —
  the landmark used both by ``screen_verify`` for FSM detection and by
  the analyze rule that pushes ``deals.sign_in``.
* ``has_yellow_glow_in_bbox_percent`` on the labeled ``deals.sign_in.free``
  bbox returns True on the reference screenshot (day 1 is claimable).
* ``find_yellow_glow_squares`` filtered to that bbox returns at least one
  hit — the DSL ``while_match`` step would advance.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from layout.yellow_glow_detector import (
    find_yellow_glow_squares,
    has_yellow_glow_in_bbox_percent,
)

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[3]
REFERENCES_DIR = MODULE_DIR / "references"
FREE_REGION = "deals.sign_in.free"
TITLE_REGION = "deals.sign_in.title"
TO_HOME_AND_BEYOND_REGION = "sign_in.to.home_and_beyound"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.fixture(scope="module")
def free_bbox(area_doc: dict) -> dict:
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == FREE_REGION:
                return region["bbox"]
    pytest.fail(f"{FREE_REGION!r} not in area doc")


@pytest.mark.asyncio
async def test_sign_in_title_landmark_detected(area_doc: dict) -> None:
    """``deals.sign_in.title`` matches its template crop on the reference.

    This is the landmark used by ``routes/screen_verify.yaml`` to identify
    the ``deals.sign_in`` screen and by ``analyze/pages/sign_in.yaml`` to
    decide whether to push the claim scenario.
    """
    frame = _load_bgr("deals.sign_in.png")

    rule = {
        "name": "deals.sign_in.page",
        "region": TITLE_REGION,
        "action": "findIcon",
        "threshold": 0.9,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule], current_screen="deals.sign_in",
    )
    hit = out["deals.sign_in.page"]
    assert hit["matched"] is True, (
        f"[{TITLE_REGION}] landmark not detected on deals.sign_in.png – row: {hit}"
    )


@pytest.mark.asyncio
async def test_sign_in_to_home_and_beyond_has_red_dot(area_doc: dict) -> None:
    """The sign_in → home_and_beyond tab carries a red dot on the reference.

    Production analyze rule ``deals.sign_in.to.home_and_beyond.has_red_dot``
    uses ``isRedDot: true`` on this fixed region to gate the cross-tab
    navigation scenario; this test pins the labelled state of the captured
    reference so a future relabel that shifts the bbox away from the dot
    fails loudly.
    """
    frame = _load_bgr("deals.sign_in.png")

    rule = {
        "name": "deals.sign_in.to.home_and_beyond.has_red_dot",
        "region": TO_HOME_AND_BEYOND_REGION,
        "isRedDot": True,
    }
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT, [rule], current_screen="deals.sign_in",
    )
    hit = out["deals.sign_in.to.home_and_beyond.has_red_dot"]
    assert hit["matched"] is True, (
        f"[{TO_HOME_AND_BEYOND_REGION}] no red dot on deals.sign_in.png – row: {hit}"
    )
    assert bool(hit.get("red_dot_present")) is True


def test_sign_in_free_box_has_glow(free_bbox: dict) -> None:
    """The Free-column free-reward region must report a glow on the reference."""
    frame = _load_bgr("deals.sign_in.png")
    assert has_yellow_glow_in_bbox_percent(frame, free_bbox) is True, (
        f"no yellow glow detected inside {FREE_REGION} bbox={free_bbox}"
    )


def test_at_least_one_claim_tile_inside_free_region(free_bbox: dict) -> None:
    """Whole-frame scan returns at least one claim tile within the Free bbox.

    Day 1 is the first claimable reward on the reference. If a future screen
    has multiple consecutive claims pending, this just asserts ``>= 1``;
    counting is left to the DSL loop (it will click one tile per iteration
    until ``find_yellow_glow_squares`` returns empty).
    """
    frame = _load_bgr("deals.sign_in.png")
    squares = find_yellow_glow_squares(frame)

    x0, y0 = free_bbox["x"], free_bbox["y"]
    x1 = x0 + free_bbox["width"]
    y1 = y0 + free_bbox["height"]
    inside = [
        s
        for s in squares
        if x0 <= s.bbox_percent["x"] + s.bbox_percent["width"] / 2 <= x1
        and y0 <= s.bbox_percent["y"] + s.bbox_percent["height"] / 2 <= y1
    ]

    assert inside, (
        f"no claim tiles inside {FREE_REGION} – all squares: "
        f"{[(round(s.bbox_percent['x'], 1), round(s.bbox_percent['y'], 1)) for s in squares]}"
    )
