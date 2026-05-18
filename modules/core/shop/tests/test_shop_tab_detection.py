"""Tests: shop screen landmarks across reference screenshots.

Covered assertions
------------------
* shop.tab.next_page is detected on the dawn_market reference.
* Each versioned shop screen (v1–v4) detects its specific content-title
  region — the landmark used by screen_verify to distinguish sub-nodes.

Per-tab detection (shop.tab.1/2/3) was removed in favour of programmatic
tab-strip segmentation; tests for that will live alongside the new detector.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
AREA_PATH = MODULE_DIR / "area.yaml"


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
async def test_v1_next_page_detected(area_doc: dict) -> None:
    """shop.tab.next_page is found on the primary v1 reference."""
    frame = _load_bgr("page.shop.dawn_market.png")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [{"name": "next_page", "region": "shop.tab.next_page", "action": "exist", "threshold": 0.7}],
        current_screen="shop.dawn_market",
    )

    row = out["next_page"]
    assert row["matched"] is True, f"[dawn_market] shop.tab.next_page not detected – row: {row}"


@pytest.mark.asyncio
async def test_v1_dawn_market_overlay_page_rule(area_doc: dict) -> None:
    """shop.dawn_market.page overlay rule matches on v1 when current_screen is shop.dawn_market."""
    frame = _load_bgr("page.shop.dawn_market.png")

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [
            {
                "name": "shop.dawn_market.page",
                "region": "page.shop.dawn_market.title",
                "action": "findIcon",
                "threshold": 0.9,
            }
        ],
        current_screen="shop.dawn_market",
    )

    row = out["shop.dawn_market.page"]
    assert row["matched"] is True, f"[dawn_market] page.shop.dawn_market.title not detected – row: {row}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_screenshot", "tab_region", "current_screen"),
    [
        # Each tab region is annotated on whichever source page the tab is
        # visible from (active or inactive). The test loads the source page
        # and verifies findIcon picks up its own crop — a self-match sanity
        # check. Cross-page identification (matching the same template on a
        # different source) lives in the identifier tests.
        ("page.shop.dawn_market.png",          "page.shop.dawn_market.title",          "shop.dawn_market"),
        ("page.shop.dawn_market.png",          "page.shop.training.title",             "shop.dawn_market"),
        ("page.shop.dawn_market.png",          "page.shop.rise_of_the_city.title",     "shop.dawn_market"),
        ("page.shop.daily_deals.png",          "page.shop.daily_deals.title",          "shop.daily_deals"),
        ("page.shop.daily_deals.png",          "page.shop.construction_queue.title",   "shop.daily_deals"),
        ("page.shop.mix_match.png",            "shop.to.mix_match",                   "shop.mix_match"),
        ("page.shop.mix_match.png",            "page.shop.chief_stamina.title",         "shop.mix_match"),
        ("page.shop.mix_match.png",            "shop.to.ice_conqueror",               "shop.mix_match"),
        ("page.shop.dawn_fund.png",            "page.shop.dawn_fund.title",            "shop.dawn_fund"),
        ("page.shop.weekly_monthly_cards.png", "page.shop.weekly_monthly_cards.title", "shop.weekly_monthly_cards"),
        ("page.shop.regular_pack.png",         "page.shop.regular_pack.title",         "shop.regular_pack"),
        ("page.shop.get_gems.png",             "page.shop.get_gems.title",             "shop.get_gems"),
    ],
)
async def test_tab_region_self_match(
    source_screenshot: str,
    tab_region: str,
    current_screen: str,
    area_doc: dict,
) -> None:
    """Each tab region matches itself on its source page."""
    frame = _load_bgr(source_screenshot)

    out = await evaluate_overlay_rules_async(
        frame,
        area_doc,
        REPO_ROOT,
        [{"name": "tab", "region": tab_region, "action": "exist", "threshold": 0.7}],
        current_screen=current_screen,
    )

    row = out["tab"]
    assert row["matched"] is True, (
        f"[{source_screenshot}] tab region {tab_region!r} not detected – row: {row}"
    )
