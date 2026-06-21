"""Unit tests for layout.tabs_strip_segmenter against the shop references.

The shop tabs strip exercises every behaviour the segmenter must get right:

* v1: 3 tabs visible, leftmost (chest) active, all three have red dots —
  validates dot-to-tab assignment across the full strip width.
* v2: 3 tabs visible, middle (chest) active, no red dots — validates that
  shifting the active capsule to the middle still anchors the grid.
* v3: 3 tabs visible, leftmost (chest) active, only chest has dot.
* v4: 4 tabs visible (incl. partial Cards on the left), 3rd (chest) active,
  only chest has dot — validates partial-tab inclusion on the left edge.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import pytest

from layout.area_manifest import load_area_doc
from layout.tabs_strip_navigator import StripAction, pick_next_strip_action
from layout.tabs_strip_segmenter import detect_tabs_in_strip

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOP_REFS = REPO_ROOT / "games" / "wos" / "core" / "shop" / "references"
STRIP_REGION = "shop.tabs_strip"


@pytest.fixture(scope="module")
def strip_bbox() -> dict:
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for reg in screen.get("regions", []):
            if reg["name"] == STRIP_REGION:
                return reg["bbox"]
    pytest.fail(f"{STRIP_REGION!r} not found in area doc")


def _load(name: str):
    img = cv2.imread(str(SHOP_REFS / name))
    assert img is not None, f"failed to load {name}"
    return img


@pytest.mark.parametrize(
    ("screenshot", "expected_n", "expected_active_idx", "expected_dots"),
    [
        ("page.shop.dawn_market.png", 3, 0, [True, True, True]),
        ("page.shop.daily_deals.png", 3, 1, [False, True, False]),
        ("page.shop.mix_match.png", 3, 0, [True, False, False]),
        ("page.shop.dawn_fund.png", 4, 2, [False, False, True, False]),
    ],
)
def test_shop_tab_segmentation(
    screenshot: str,
    expected_n: int,
    expected_active_idx: int,
    expected_dots: list[bool],
    strip_bbox: dict,
) -> None:
    img = _load(screenshot)
    tabs = detect_tabs_in_strip(img, strip_bbox)

    assert len(tabs) == expected_n, (
        f"{screenshot}: expected {expected_n} tabs, got {len(tabs)} "
        f"(bboxes: {[t.bbox_percent for t in tabs]})"
    )

    active_indices = [t.index for t in tabs if t.active]
    assert active_indices == [expected_active_idx], (
        f"{screenshot}: expected exactly tab {expected_active_idx} active, got {active_indices}"
    )

    actual_dots = [t.has_red_dot for t in tabs]
    assert actual_dots == expected_dots, (
        f"{screenshot}: red_dot mismatch — got {actual_dots}, expected {expected_dots}"
    )
    assert tabs[expected_active_idx].color_state == "active_light"
    for t in tabs:
        if not t.active:
            assert t.color_state in {"inactive_blue", "inactive_unknown"}


def test_indices_are_left_to_right(strip_bbox: dict) -> None:
    """Indices increase strictly with bbox X across all references."""
    for name in (
        "page.shop.dawn_market.png",
        "page.shop.daily_deals.png",
        "page.shop.mix_match.png",
        "page.shop.dawn_fund.png",
    ):
        img = _load(name)
        tabs = detect_tabs_in_strip(img, strip_bbox)
        xs = [t.bbox_percent["x"] for t in tabs]
        assert xs == sorted(xs), f"{name}: tab bbox X not monotonically increasing: {xs}"
        assert [t.index for t in tabs] == list(range(len(tabs))), (
            f"{name}: tab indices not 0..N-1"
        )


def test_exactly_one_active_per_strip(strip_bbox: dict) -> None:
    """Across all shop refs, exactly one tab is marked active per strip."""
    for name in (
        "page.shop.dawn_market.png",
        "page.shop.daily_deals.png",
        "page.shop.mix_match.png",
        "page.shop.dawn_fund.png",
    ):
        img = _load(name)
        tabs = detect_tabs_in_strip(img, strip_bbox)
        n_active = sum(1 for t in tabs if t.active)
        assert n_active == 1, f"{name}: expected 1 active tab, got {n_active}"


def test_shop_blue_run_fallback_keeps_click_inside_red_dot_tab(strip_bbox: dict) -> None:
    """Some Shop product pages have no reliable white active tab in the strip.

    The segmenter must fall back to visible blue tab bodies rather than using a
    tiny text fragment as pitch; otherwise it creates many skinny slots and
    clicks the border just right of ``Daily Deals``.
    """
    img = _load("page.shop.fire_crystal_pack.png")
    tabs = detect_tabs_in_strip(img, strip_bbox)

    assert len(tabs) == 4
    assert [t.has_red_dot for t in tabs] == [False, False, True, False]
    assert {t.segment_source for t in tabs} == {"capsule_runs"}
    assert tabs[2].color_state == "inactive_blue"
    assert pick_next_strip_action(tabs) == StripAction("click_tab", tab_index=2)

    daily = tabs[2].bbox_percent
    center_x = (daily["x"] + daily["width"] / 2.0) / 100.0 * 720
    assert 330 <= center_x <= 380


def test_tap_bbox_is_capsule_tight(strip_bbox: dict) -> None:
    """Each tab exposes a ``tap_bbox_percent`` narrowed to the capsule rows.

    Clicks use it instead of the full strip bbox so the tap (and its jitter box)
    stays on the tab body rather than the padding above/below the strip. The
    tightening is vertical only — x/width are untouched so template-identification
    crops, which need the full-height region, keep using ``bbox_percent``.
    """
    img = _load("page.shop.dawn_fund.png")
    tabs = detect_tabs_in_strip(img, strip_bbox)
    assert tabs

    for t in tabs:
        assert t.tap_bbox_percent is not None, t
        tap, full = t.tap_bbox_percent, t.bbox_percent
        # vertical-only tightening
        assert abs(tap["x"] - full["x"]) < 1e-6
        assert abs(tap["width"] - full["width"]) < 1e-6
        # tap box is contained within, and no taller than, the full bbox
        assert tap["height"] <= full["height"] + 1e-6
        assert tap["y"] >= full["y"] - 1e-6
        assert tap["y"] + tap["height"] <= full["y"] + full["height"] + 1e-6

    # The padding really is trimmed — at least one tab is meaningfully tighter.
    assert any(
        t.tap_bbox_percent["height"] < t.bbox_percent["height"] - 0.5 for t in tabs
    )
