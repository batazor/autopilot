"""Tab-strip segmentation + active-tab identification per shop page.

Three invariants per reference screenshot, locked in for the 8 shop pages:

* **Tab count** — segmenter detects the expected number of tabs in the strip.
* **Single active** — exactly one tab carries ``active=True`` (the white capsule
  anchor). Multiple actives would mean the anchor latched onto noise.
* **Active identity** — that tab, fed through the template identifier, comes
  back as the page we know the bot is on. Catches regressions where a future
  crop change makes the active capsule template ambiguous with siblings.

construction_queue is xfailed on the last assertion: the only available
template is taken from the daily_deals page (where construction_queue appears
as an *inactive* blue tab). On its own page it's *active* (white capsule);
the cross-state NCC drops below 0.7. Annotating a second ``shop.to.construction_queue``
on construction_queue.png itself would lift the xfail.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from layout.area_manifest import load_area_doc
from layout.tabs_strip_identifier import identify_tabs_by_template
from layout.tabs_strip_segmenter import detect_tabs_in_strip

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"

# (screen_id, screenshot, expected_tab_count, expected_active_index)
PAGES = [
    ("shop.dawn_market",          "page.shop.dawn_market.png",          3, 0),
    ("shop.daily_deals",          "page.shop.daily_deals.png",          3, 1),
    ("shop.mix_match",            "page.shop.mix_match.png",            3, 0),
    ("shop.dawn_fund",            "page.shop.dawn_fund.png",            4, 2),
    ("shop.construction_queue",   "page.shop.construction_queue.png",   3, 1),
    ("shop.get_gems",             "page.shop.get_gems.png",             4, 3),
    ("shop.regular_pack",         "page.shop.regular_pack.png",         3, 1),
    ("shop.weekly_monthly_cards", "page.shop.weekly_monthly_cards.png", 3, 0),
]


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
    """Auto-discover ``page_id → template_bgr`` from shop area regions.

    Pulls every region whose bbox falls inside the tab strip's Y range — both
    the navigation-style ``shop.to.<X>`` regions on sub-pages and the
    title-style ``page.shop.<X>.title`` regions on the shop hub. Page-body
    titles (y > strip_bottom) are excluded because they're identifiers of
    the open page, not tab icons.
    """
    crop_dir = REFERENCES_DIR / "crop"
    strip_y_lo = strip_bbox["y"]
    strip_y_hi = strip_y_lo + strip_bbox["height"]
    templates: dict = {}
    for s in area_doc.get("screens", []):
        ocr = str(s.get("ocr", ""))
        if "modules/core/shop" not in ocr:
            continue
        stem = Path(ocr).stem
        for reg in s.get("regions", []):
            name = reg.get("name", "")
            bbox = reg.get("bbox", {})
            if not (strip_y_lo <= bbox.get("y", 0) < strip_y_hi):
                continue
            if name.startswith("shop.to."):
                page_id = "shop." + name[len("shop.to."):]
            elif name.startswith("page.shop.") and name.endswith(".title"):
                suffix = name[len("page.shop."):-len(".title")]
                if not suffix:
                    continue
                page_id = "shop." + suffix
            else:
                continue
            crop = crop_dir / f"{stem}_{name}.png"
            if crop.is_file() and page_id not in templates:
                img = cv2.imread(str(crop))
                if img is not None:
                    templates[page_id] = img
    return templates


@pytest.mark.parametrize(("screen_id", "screenshot", "expected_n", "expected_active"), PAGES)
def test_tab_count(screen_id, screenshot, expected_n, expected_active, strip_bbox):
    """Strip segmenter detects the expected number of tabs."""
    img = _load_bgr(screenshot)
    tabs = detect_tabs_in_strip(img, strip_bbox)
    assert len(tabs) == expected_n, (
        f"[{screen_id}] expected {expected_n} tabs, got {len(tabs)} "
        f"(bboxes: {[t.bbox_percent for t in tabs]})"
    )


@pytest.mark.parametrize(("screen_id", "screenshot", "expected_n", "expected_active"), PAGES)
def test_exactly_one_active(screen_id, screenshot, expected_n, expected_active, strip_bbox):
    """Exactly one tab is flagged active, and it's the expected index."""
    img = _load_bgr(screenshot)
    tabs = detect_tabs_in_strip(img, strip_bbox)
    actives = [t.index for t in tabs if t.active]
    assert actives == [expected_active], (
        f"[{screen_id}] expected exactly tab {expected_active} active, got {actives}"
    )


@pytest.mark.parametrize(
    ("screen_id", "screenshot", "expected_n", "expected_active"),
    [
        pytest.param(
            *p,
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "shop.to.construction_queue template is annotated on the "
                    "daily_deals page (inactive blue tab) — its NCC against the "
                    "active white capsule on construction_queue.png drops below "
                    "0.7. Lift by annotating shop.to.construction_queue on "
                    "construction_queue.png as well."
                ),
            ),
        )
        if p[0] == "shop.construction_queue"
        else p
        for p in PAGES
    ],
)
def test_active_tab_identifies_as_own_page(
    screen_id, screenshot, expected_n, expected_active, strip_bbox, page_templates
):
    """The active tab, identified via template match, equals the page we're on.

    Ties the segmenter + identifier together: not only must the active capsule
    be found at the right slot, its template content must also uniquely point
    back to ``screen_id``.
    """
    img = _load_bgr(screenshot)
    tabs = detect_tabs_in_strip(img, strip_bbox)
    active = next((t for t in tabs if t.active), None)
    assert active is not None, f"[{screen_id}] no active tab detected"
    ids = identify_tabs_by_template(img, tabs, page_templates)
    assert ids.get(active.index) == screen_id, (
        f"[{screen_id}] active tab [{active.index}] identified as "
        f"{ids.get(active.index)!r}, expected {screen_id!r}"
    )
