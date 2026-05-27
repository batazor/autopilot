"""TDD spec for the shop navigation flow over the tabs strip.

User-described flow being locked in:

  1. main_city: click ``main_city.to.shop`` (it has the red dot) → arrive at
     ``shop``; page is detected as ``shop.dawn_market`` (v1 reference).
  2. Run ``shop.dawn_market`` scenario — clears the active (chest) tab's dot.
  3. Strip still has red dots on inactive tabs 1 and 2 → click tab 1, run its
     scenario, then click tab 2, run its scenario.
  4. Strip has no more inactive red dots → click ``shop.tab.next_page``.
  5. Arrive at v2 (``shop.daily_deals``) → run scenario → no more inactive
     red dots → ``next_page``.
  6. Arrive at v4 (``shop.dawn_fund``) → run scenario → ``next_page``.
  7. Arrive at v3 (``shop.mix_match``) → run scenario → done.

The lower-level decision under test here is what the navigator picks given a
segmented strip. The higher-level flow (which scenario fires, which page
appears after ``next_page``) is asserted in :func:`test_shop_flow_v1_to_v3`,
which walks the four reference frames in the order the user described.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import cv2
import pytest

from layout.area_manifest import load_area_doc
from layout.tabs_strip_navigator import StripAction, pick_next_strip_action
from layout.tabs_strip_segmenter import TabDetection, detect_tabs_in_strip

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOP_REFS = REPO_ROOT / "games" / "wos" / "core" / "shop" / "references"


# ── Decision logic (no frames) ──────────────────────────────────────────────


def _tab(index: int, *, active: bool, dot: bool) -> TabDetection:
    """Synthetic TabDetection with a sentinel bbox — only flags matter here."""
    return TabDetection(
        index=index,
        bbox_percent={"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0},
        active=active,
        has_red_dot=dot,
    )


def test_picks_first_non_active_red_dot_tab() -> None:
    """After current page's scenario fires, click the next dotted inactive tab."""
    tabs = [
        _tab(0, active=True, dot=False),   # cleared by scenario
        _tab(1, active=False, dot=True),
        _tab(2, active=False, dot=True),
    ]
    assert pick_next_strip_action(tabs) == StripAction("click_tab", tab_index=1)


def test_skips_active_even_if_dot_still_present() -> None:
    """Active tab is the responsibility of the page scenario, not the navigator."""
    tabs = [
        _tab(0, active=True, dot=True),   # scenario hasn't cleared it yet
        _tab(1, active=False, dot=True),
    ]
    # We still prefer the inactive dotted tab — the active dot is handled
    # by the scenario's own steps, not by a re-click on the same page.
    assert pick_next_strip_action(tabs) == StripAction("click_tab", tab_index=1)


def test_no_dotted_inactive_tabs_advances_page() -> None:
    """When only the active tab carries (or carried) a dot, move to next page."""
    tabs = [
        _tab(0, active=True, dot=False),
        _tab(1, active=False, dot=False),
        _tab(2, active=False, dot=False),
    ]
    assert pick_next_strip_action(tabs) == StripAction("advance_page")


def test_empty_strip_signals_done() -> None:
    """No tabs detected → navigator has nothing to do."""
    assert pick_next_strip_action([]) == StripAction("done")


def test_left_to_right_priority() -> None:
    """Multiple dotted inactive tabs → leftmost wins."""
    tabs = [
        _tab(0, active=False, dot=False),
        _tab(1, active=True, dot=False),
        _tab(2, active=False, dot=True),
        _tab(3, active=False, dot=True),
    ]
    assert pick_next_strip_action(tabs) == StripAction("click_tab", tab_index=2)


# ── Flow walk-through over the 4 shop reference frames ──────────────────────


@pytest.fixture(scope="module")
def strip_bbox() -> dict:
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for reg in screen.get("regions", []):
            if reg["name"] == "shop.tabs_strip":
                return reg["bbox"]
    pytest.fail("shop.tabs_strip not in area doc")


def _detect(name: str, strip_bbox: dict) -> list[TabDetection]:
    img = cv2.imread(str(SHOP_REFS / name))
    assert img is not None, f"missing reference: {name}"
    return detect_tabs_in_strip(img, strip_bbox)


def _clear_active_dot(tabs: list[TabDetection]) -> list[TabDetection]:
    """Simulate a page scenario clearing the active tab's red dot."""
    return [replace(t, has_red_dot=False) if t.active else t for t in tabs]


def test_shop_flow_v1_to_v3(strip_bbox: dict) -> None:
    """Walk the full navigation contract over the four reference frames.

    The 4 reference shots only capture the *initial* state of each sub-shop
    page; we don't have screenshots of "post-scenario" states. So at each
    step we simulate the scenario clearing the active tab's dot via
    :func:`_clear_active_dot`, then assert the navigator's next pick.
    """
    # ── Arrival: v1 / shop.dawn_market ─────────────────────────────────────
    v1_initial = _detect("page.shop.dawn_market.png", strip_bbox)
    assert any(t.active and t.has_red_dot for t in v1_initial), (
        "v1 must show the active chest tab carrying a red dot — that's what "
        "triggers the shop.dawn_market scenario from analyze.yaml"
    )

    # Scenario clears chest dot. Inactive tabs still have dots → click leftmost.
    v1_post = _clear_active_dot(v1_initial)
    decision = pick_next_strip_action(v1_post)
    assert decision.kind == "click_tab"
    assert decision.tab_index == 1, (
        f"after dawn_market scenario, navigator should click first inactive "
        f"dotted tab (index 1); got {decision}"
    )

    # Simulate handling tab 1's notification — bot navigates, scenario runs,
    # returns. We don't have a screenshot for that, so collapse tab 1's dot
    # in the model and re-decide on the remaining state.
    v1_after_tab1 = [
        replace(t, has_red_dot=False) if t.index == 1 else t for t in v1_post
    ]
    decision = pick_next_strip_action(v1_after_tab1)
    assert decision == StripAction("click_tab", tab_index=2), (
        f"after handling tab 1, expected click on tab 2; got {decision}"
    )

    # Tab 2 cleared → strip has no inactive dots → advance to next page.
    v1_drained = [replace(t, has_red_dot=False) for t in v1_post]
    assert pick_next_strip_action(v1_drained) == StripAction("advance_page"), (
        "strip fully drained on v1 → navigator should hand off to next_page click"
    )

    # ── v2 / shop.daily_deals ──────────────────────────────────────────────
    v2_initial = _detect("page.shop.daily_deals.png", strip_bbox)
    # Only the active tab carries a dot in the v2 ref (no inactive dots).
    # Scenario clears it → immediately advance.
    v2_post = _clear_active_dot(v2_initial)
    assert pick_next_strip_action(v2_post) == StripAction("advance_page")

    # ── v4 / shop.dawn_fund ────────────────────────────────────────────────
    v4_initial = _detect("page.shop.dawn_fund.png", strip_bbox)
    v4_post = _clear_active_dot(v4_initial)
    assert pick_next_strip_action(v4_post) == StripAction("advance_page")

    # ── v3 / shop.mix_match ────────────────────────────────────────────────
    v3_initial = _detect("page.shop.mix_match.png", strip_bbox)
    v3_post = _clear_active_dot(v3_initial)
    # End of the loop the user described — once mix_match is processed and
    # there are no inactive dots, the caller stops cycling (the contract on
    # whether we treat this as advance_page or done is the caller's; from
    # the navigator's view there's no inactive dotted tab left).
    assert pick_next_strip_action(v3_post) == StripAction("advance_page")
