"""Navigation contract: from each shop sub-page, which tabs can the bot click
to reach which destination page?

Builds the full segment → identify → click-target pipeline:

  source page screenshot
    → detect_tabs_in_strip          (segments the strip dynamically)
    → identify_tabs_by_template     (matches each tab against the template lib)
    → reachable[target_page] = tab bbox
    → bot taps inside that bbox to navigate

Each declared route asserts: the target page IS identifiable on the source's
strip, sits at the expected tab index, and its bbox center lands at the
expected X% (±1.5%) of the frame. Tolerance absorbs sub-pixel rounding from
``patch_bgr_from_bbox_percent`` and the segmenter's grid step but rejects a
silent geometry drift that would shift the click into a neighbour.

The user-described example is ``shop.construction_queue → shop.daily_deals``:
on the construction_queue page, the daily_deals tab is the rightmost visible
inactive tab (index 2). Tapping inside its bbox navigates to daily_deals,
where the page scenario takes over.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from layout.area_manifest import load_area_doc
from layout.tabs_strip_identifier import (
    discover_shop_tab_templates,
    identify_tabs_by_template,
)
from layout.tabs_strip_segmenter import detect_tabs_in_strip

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"

# (source_page, source_screenshot, target_page, expected_tab_index, expected_center_x_pct)
NAVIGATION_ROUTES = [
    ("shop.dawn_market",        "page.shop.dawn_market.png",        "shop.training",             1, 46.9),
    ("shop.dawn_market",        "page.shop.dawn_market.png",        "shop.rise_of_the_city",     2, 75.0),
    ("shop.daily_deals",        "page.shop.daily_deals.png",        "shop.construction_queue",   0, 21.9),
    ("shop.daily_deals",        "page.shop.daily_deals.png",        "shop.weekly_monthly_cards", 2, 78.1),
    ("shop.mix_match",          "page.shop.mix_match.png",          "shop.chief_stamina",        1, 46.9),
    ("shop.mix_match",          "page.shop.mix_match.png",          "shop.ice_conqueror",        2, 75.0),
    ("shop.dawn_fund",          "page.shop.dawn_fund.png",          "shop.regular_pack",         1, 25.4),
    ("shop.dawn_fund",          "page.shop.dawn_fund.png",          "shop.get_gems",             3, 79.8),
    ("shop.construction_queue", "page.shop.construction_queue.png", "shop.dawn_market",          0, 18.5),
    ("shop.construction_queue", "page.shop.construction_queue.png", "shop.daily_deals",          2, 74.6),
    ("shop.get_gems",           "page.shop.get_gems.png",           "shop.regular_pack",         1, 28.8),
    ("shop.regular_pack",       "page.shop.regular_pack.png",       "shop.weekly_monthly_cards", 0, 20.1),
    ("shop.weekly_monthly_cards","page.shop.weekly_monthly_cards.png","shop.regular_pack",       1, 48.5),
]

CLICK_X_TOLERANCE_PCT = 1.5
"""Maximum allowed drift of the computed tab-center X from the recorded value.

Tabs are ~28% wide; a 1.5% slack covers integer rounding inside
``patch_bgr_from_bbox_percent`` and a small grid-step nudge from the
segmenter without letting a tab boundary cross into a neighbour."""


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.fixture(scope="module")
def strip_bbox(area_doc: dict) -> dict:
    for s in area_doc.get("screens", []):
        for r in s.get("regions", []):
            if r.get("name") == "shop.tabs_strip":
                return r["bbox"]
    pytest.fail("shop.tabs_strip not in area doc")


@pytest.fixture(scope="module")
def page_templates(area_doc: dict, strip_bbox: dict) -> dict:
    """Tab templates via production discovery (``shop.to`` + ``page.to`` + hub titles)."""
    return discover_shop_tab_templates(area_doc, REPO_ROOT, strip_bbox)


@pytest.mark.parametrize(
    ("source_page", "source_png", "target_page", "expected_idx", "expected_cx"),
    NAVIGATION_ROUTES,
)
def test_navigation_route_click_target(
    source_page: str,
    source_png: str,
    target_page: str,
    expected_idx: int,
    expected_cx: float,
    strip_bbox: dict,
    page_templates: dict,
) -> None:
    """Bot can compute a click target to reach ``target_page`` from ``source_page``."""
    frame = _load_bgr(source_png)
    tabs = detect_tabs_in_strip(frame, strip_bbox)
    ids = identify_tabs_by_template(frame, tabs, page_templates)

    # Find the tab identified as the target.
    target_tab = next(
        (t for t in tabs if ids.get(t.index) == target_page), None
    )
    assert target_tab is not None, (
        f"[{source_page} → {target_page}] target tab not identified; "
        f"detected ids: { {t.index: ids.get(t.index) for t in tabs} }"
    )

    # Index matches the expected slot (catches re-ordering / grid shifts).
    assert target_tab.index == expected_idx, (
        f"[{source_page} → {target_page}] expected tab index {expected_idx}, "
        f"got {target_tab.index}"
    )

    # Click target = bbox center. The DSL click path adds inset random jitter
    # at tap time; here we only assert the bbox center is where we expect it.
    b = target_tab.bbox_percent
    cx = b["x"] + b["width"] / 2.0
    assert abs(cx - expected_cx) <= CLICK_X_TOLERANCE_PCT, (
        f"[{source_page} → {target_page}] tab center_x={cx:.2f}% drifted from "
        f"expected {expected_cx:.2f}% (±{CLICK_X_TOLERANCE_PCT}%)"
    )

    # Sanity: the click target sits inside the tabs strip, not above/below it.
    cy = b["y"] + b["height"] / 2.0
    strip_y_lo, strip_y_hi = strip_bbox["y"], strip_bbox["y"] + strip_bbox["height"]
    assert strip_y_lo <= cy <= strip_y_hi, (
        f"[{source_page} → {target_page}] tab center_y={cy:.2f}% outside "
        f"strip Y range [{strip_y_lo:.2f}, {strip_y_hi:.2f}]"
    )


def test_construction_queue_to_daily_deals_user_example(strip_bbox, page_templates):
    """Focal user example: from construction_queue page, daily_deals tab is on the right."""
    frame = _load_bgr("page.shop.construction_queue.png")
    tabs = detect_tabs_in_strip(frame, strip_bbox)
    ids = identify_tabs_by_template(frame, tabs, page_templates)
    target = next((t for t in tabs if ids.get(t.index) == "shop.daily_deals"), None)
    assert target is not None, (
        "shop.daily_deals tab not identifiable on construction_queue.png "
        f"(detected: {ids})"
    )
    cx = target.bbox_percent["x"] + target.bbox_percent["width"] / 2.0
    assert 60 < cx < 90, f"daily_deals tab center expected on right half, got {cx:.1f}%"
