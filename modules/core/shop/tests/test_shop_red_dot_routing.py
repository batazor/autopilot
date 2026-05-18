"""Analyzer contract: detectTabs surfaces *which* sub-shop pages need work.

The overlay-engine ``detectTabs`` action returns, per tab:

* ``page_id`` — identified sub-shop the tab navigates to (None if no template).
* ``has_red_dot`` — notification badge present.
* ``active`` — currently-selected tab (anchored on the white capsule).

Plus the aggregate ``red_dot_pages`` — page IDs of every *inactive* tab that
carries a red dot. The active page's own dot is filtered out because that
page's scenario clears it from inside; re-pushing it would loop.

On ``construction_queue.png`` the strip shows three tabs:
  tab 0 = dawn_market (red dot), tab 1 = active construction_queue,
  tab 2 = daily_deals (red dot).
So the bot — after running ``shop.construction_queue`` — should also queue
``shop.dawn_market`` and ``shop.daily_deals`` for follow-up. The test below
locks that contract per page.
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


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


async def _run(screen_id: str, png: str, area_doc: dict) -> dict:
    frame = _load_bgr(png)
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT,
        [{"name": "tabs", "region": "shop.tabs_strip", "action": "detectTabs"}],
        current_screen=screen_id,
    )
    return out["tabs"]


# ── Focal user example ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_construction_queue_routes_to_dawn_market_and_daily_deals(
    area_doc: dict,
) -> None:
    """On construction_queue, red dots on dawn_market + daily_deals tabs →
    bot should queue both follow-up scenarios."""
    hit = await _run("shop.construction_queue", "page.shop.construction_queue.png", area_doc)
    assert set(hit["red_dot_pages"]) == {"shop.dawn_market", "shop.daily_deals"}


# ── Per-page red-dot routing matrix ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("screen_id", "screenshot", "expected_red_dot_pages"),
    [
        # construction_queue: 2 inactive dotted tabs visible
        ("shop.construction_queue", "page.shop.construction_queue.png",
         {"shop.dawn_market", "shop.daily_deals"}),
        # dawn_market (hub): training + rise_of_the_city inactive tabs both have dots.
        # The active dawn_market dot is NOT in red_dot_pages (its scenario handles itself).
        ("shop.dawn_market", "page.shop.dawn_market.png",
         {"shop.training", "shop.rise_of_the_city"}),
        # daily_deals: only the active tab has a dot → no follow-up routing
        ("shop.daily_deals", "page.shop.daily_deals.png", set()),
        # mix_match: only active has dot
        ("shop.mix_match", "page.shop.mix_match.png", set()),
        # dawn_fund: only active has dot
        ("shop.dawn_fund", "page.shop.dawn_fund.png", set()),
    ],
)
async def test_red_dot_pages_per_screen(
    screen_id: str,
    screenshot: str,
    expected_red_dot_pages: set,
    area_doc: dict,
) -> None:
    """For each tested page, red_dot_pages aggregate matches expectation."""
    hit = await _run(screen_id, screenshot, area_doc)
    actual = set(hit["red_dot_pages"])
    assert actual == expected_red_dot_pages, (
        f"[{screen_id}] red_dot_pages={actual!r}, expected={expected_red_dot_pages!r}"
    )


# ── Per-tab payload integrity ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_construction_queue_tab_payload(area_doc: dict) -> None:
    """Each tab entry carries (index, page_id, active, has_red_dot)."""
    hit = await _run("shop.construction_queue", "page.shop.construction_queue.png", area_doc)
    tabs = {t["index"]: t for t in hit["tabs"]}
    # Three tabs detected.
    assert set(tabs) == {0, 1, 2}
    # Tab 0: dawn_market, inactive, red_dot
    assert tabs[0]["page_id"] == "shop.dawn_market"
    assert tabs[0]["active"] is False
    assert tabs[0]["has_red_dot"] is True
    # Tab 1: active (construction_queue is xfail-identified — page_id may be None)
    assert tabs[1]["active"] is True
    # Tab 2: daily_deals, inactive, red_dot
    assert tabs[2]["page_id"] == "shop.daily_deals"
    assert tabs[2]["active"] is False
    assert tabs[2]["has_red_dot"] is True


@pytest.mark.asyncio
async def test_dawn_market_active_page_id_set(area_doc: dict) -> None:
    """active_page_id should be the identified page of the active tab."""
    hit = await _run("shop.dawn_market", "page.shop.dawn_market.png", area_doc)
    assert hit["active_page_id"] == "shop.dawn_market"
