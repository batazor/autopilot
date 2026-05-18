"""Per-page title detection contract for the shop module.

Each of the 8 shop sub-pages has a ``page.shop.<page>.title`` region that
identifies the page when the bot is on it (used by analyze.yaml +
screen_verify.yaml to set the active node). Two guarantees are tested:

* **Self-match**: every title region's template matches on its own page's
  reference screenshot at threshold 0.7+ (effectively 1.00 since the crop
  comes from the same image).
* **No cross-match**: no other page's title fires on a different page's
  reference. Off-diagonal NCC sits at ≤0.40 across the 8×8 matrix —
  well below the 0.7 threshold, so a title region uniquely identifies its
  page and won't trigger a wrong-node transition.
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

PAGES: list[tuple[str, str, str]] = [
    # (screen_id, title_region, reference_screenshot)
    ("shop.dawn_market",          "page.shop.dawn_market.title",          "page.shop.dawn_market.png"),
    ("shop.daily_deals",          "page.shop.daily_deals.title",          "page.shop.daily_deals.png"),
    ("shop.mix_match",            "page.shop.mix_match.title",            "page.shop.mix_match.png"),
    ("shop.dawn_fund",            "page.shop.dawn_fund.title",            "page.shop.dawn_fund.png"),
    ("shop.construction_queue",   "page.shop.construction_queue.title",   "page.shop.construction_queue.png"),
    ("shop.weekly_monthly_cards", "page.shop.weekly_monthly_cards.title", "page.shop.weekly_monthly_cards.png"),
    ("shop.get_gems",             "page.shop.get_gems.title",             "page.shop.get_gems.png"),
    ("shop.regular_pack",         "page.shop.regular_pack.title",         "page.shop.regular_pack.png"),
]

THRESHOLD = 0.9
"""findIcon score floor — matches the production threshold in analyze.yaml.

On the 8 shop refs diagonal sits at 1.00 (templates are crops of the same
images) and the highest off-diagonal cross-match is 0.40 — the 0.9 floor
sits comfortably inside that gap so the test fails loudly if any future
edit narrows it."""


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference screenshot: {path}"
    return frame


@pytest.fixture(scope="module")
def area_doc() -> dict:
    return load_area_doc(REPO_ROOT)


@pytest.mark.asyncio
@pytest.mark.parametrize(("screen_id", "title_region", "screenshot"), PAGES)
async def test_title_self_match(
    screen_id: str, title_region: str, screenshot: str, area_doc: dict
) -> None:
    """Each page's title region matches on its own reference."""
    frame = _load_bgr(screenshot)
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT,
        [{"name": "t", "region": title_region, "action": "findIcon", "threshold": THRESHOLD}],
        current_screen=screen_id,
    )
    row = out["t"]
    assert row.get("matched") is True, (
        f"[{screenshot}] {title_region!r} did not self-match — row: {row}"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("source_screen", "source_title", "source_png", "other_screen", "other_title"),
    [
        (sn, st, sp, on, ot)
        for sn, st, sp in PAGES
        for on, ot, _ in PAGES
        if sn != on
    ],
)
async def test_no_cross_title_false_positive(
    source_screen: str,
    source_title: str,
    source_png: str,
    other_screen: str,
    other_title: str,
    area_doc: dict,
) -> None:
    """A page's title region must not fire on a different page's reference.

    Without this, screen_verify could trip from one shop sub-page to another
    on similar-but-not-identical UI — silently misclassifying which scenario
    to push.
    """
    frame = _load_bgr(source_png)
    out = await evaluate_overlay_rules_async(
        frame, area_doc, REPO_ROOT,
        [{"name": "t", "region": other_title, "action": "findIcon", "threshold": THRESHOLD}],
        current_screen=source_screen,
    )
    row = out["t"]
    assert row.get("matched") is False, (
        f"on {source_png}, {other_title!r} (from {other_screen}) wrongly matched — row: {row}"
    )
