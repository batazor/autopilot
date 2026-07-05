"""Blue CTA button detector — panel-background / grey-pill rejection.

Regression for the bs5 RU «Барак» (Shelter) bug: the building panel's pale
light-blue backdrop was registering as one giant near-square blue blob, so the
`building.upgrade` long-press landed on the *card body* instead of a button (the
selected-furniture «Улучшить» pill was greyed-out, i.e. nothing to click).

The fix: a real CTA pill is a *saturated, bright* blue lozenge — raise the mask
saturation/value floors (S≥70, V≥140) so pale panels and washed-out grey pills
are excluded, and reject near-square (merged-panel) blobs by aspect ratio.
"""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async
from layout.area_manifest import load_area_doc
from layout.blue_button_detector import find_blue_buttons

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests/fixtures/ru_shelter_grey_lower_pill.png"

# RU «Белая мгла» Shelter anchors (mirror games/wos/ru/core/building/common/area.yaml).
RU_LOWER_ANCHOR = {"x": 74.0, "y": 48.0, "width": 22.0, "height": 7.0}
RU_UPPER_ANCHOR = {"x": 76.0, "y": 39.0, "width": 20.0, "height": 5.0}

# The buggy tap the old detector produced: centre of the 266×270 panel blob,
# which sits on the card body, ~30 px left of and 90 px above the grey pill.
BUGGY_CARD_BODY_PT = (587, 659)


def _hsv_to_bgr(h: float, s: float, v: float) -> tuple[int, int, int]:
    px = np.uint8([[[int(h), int(s), int(v)]]])
    b, g, r = cv2.cvtColor(px, cv2.COLOR_HSV2BGR)[0, 0]
    return int(b), int(g), int(r)


def _synthetic_panel() -> np.ndarray:
    """A 720×1280 frame mimicking the RU building panel colour layout."""
    img = np.full((1280, 720, 3), 128, np.uint8)  # neutral grey, S=0 → not blue
    # Pale light-blue panel backdrop (the false positive): big, near-square, S≈52.
    cv2.rectangle(img, (450, 560), (700, 800), _hsv_to_bgr(110, 52, 248), -1)
    # Greyed-out (inactive) pill: wide lozenge but washed out (low S, dim V).
    cv2.rectangle(img, (505, 690), (665, 745), _hsv_to_bgr(106, 51, 108), -1)
    # Active, saturated blue pill (the only thing that should match).
    cv2.rectangle(img, (505, 470), (665, 525), _hsv_to_bgr(108, 165, 205), -1)
    return img


def test_saturated_pill_matches_pale_panel_and_grey_pill_do_not() -> None:
    img = _synthetic_panel()

    # Anchor over the active saturated pill → matched.
    active = find_blue_buttons(
        img, anchor_bbox_percent={"x": 70.0, "y": 37.0, "width": 23.0, "height": 5.0}
    )
    assert active, "saturated blue pill must be detected"
    top = active[0]
    cx = top.top_left[0] + top.width // 2
    cy = top.top_left[1] + top.height // 2
    assert 505 <= cx <= 665 and 470 <= cy <= 525, (cx, cy)
    assert top.width >= 1.4 * top.height, "a pill is wider than tall"

    # Anchor over the greyed-out pill → no hit (washed out).
    grey = find_blue_buttons(
        img,
        anchor_bbox_percent={"x": 70.0, "y": 54.0, "width": 23.0, "height": 5.0},
        y_padding_ratio=0.30,
    )
    assert grey == [], "a greyed-out (inactive) pill must not match"

    # Anchor over the pale panel backdrop → no near-square panel blob.
    panel = find_blue_buttons(
        img, anchor_bbox_percent={"x": 64.0, "y": 60.0, "width": 30.0, "height": 4.0}
    )
    assert all(h.width >= 1.4 * h.height for h in panel), [
        (h.width, h.height) for h in panel
    ]


def test_near_square_blob_rejected_by_aspect_gate() -> None:
    """A near-square blue blob (merged panel) is never a button."""
    img = np.full((1280, 720, 3), 128, np.uint8)
    # Square saturated-blue region — passes colour but not the pill aspect.
    cv2.rectangle(img, (460, 540), (700, 790), _hsv_to_bgr(108, 160, 220), -1)
    hits = find_blue_buttons(
        img, anchor_bbox_percent={"x": 74.0, "y": 48.0, "width": 22.0, "height": 7.0}
    )
    assert hits == [], "near-square blob must be rejected"


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture frame missing")
def test_ru_grey_lower_pill_yields_no_card_body_tap() -> None:
    img = cv2.imread(str(FIXTURE))
    assert img is not None

    # With the per-row padding from area.yaml, the grey lower pill yields nothing.
    scoped = find_blue_buttons(
        img, anchor_bbox_percent=RU_LOWER_ANCHOR, y_padding_ratio=0.30
    )
    assert scoped == [], "grey lower pill must not match"

    # Even with the default (wide) padding, the old square-panel false positive
    # is gone: any surviving blob is button-shaped, never the card-body blob.
    wide = find_blue_buttons(img, anchor_bbox_percent=RU_LOWER_ANCHOR)
    for h in wide:
        assert h.width >= 1.4 * h.height, (h.width, h.height)
        cx = h.top_left[0] + h.width // 2
        cy = h.top_left[1] + h.height // 2
        # Never the buggy card-body point.
        assert not (
            abs(cx - BUGGY_CARD_BODY_PT[0]) < 25
            and abs(cy - BUGGY_CARD_BODY_PT[1]) < 40
        ), (cx, cy)


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture frame missing")
def test_ru_upper_active_button_tightly_matched() -> None:
    img = cv2.imread(str(FIXTURE))
    assert img is not None

    hits = find_blue_buttons(
        img, anchor_bbox_percent=RU_UPPER_ANCHOR, y_padding_ratio=0.35
    )
    assert hits, "active upper «Улучшить» pill must be detected"
    top = hits[0]
    assert top.width >= 1.4 * top.height, (top.width, top.height)
    assert top.height < 120, "blob must be tight on the pill, not merged with panel"
    cx = top.top_left[0] + top.width // 2
    cy = top.top_left[1] + top.height // 2
    # On the upper «Улучшить» pill (x≈484–690, y≈497–570 on the fixture).
    assert 540 <= cx <= 660 and 497 <= cy <= 572, (cx, cy)


@pytest.mark.skipif(not FIXTURE.is_file(), reason="fixture frame missing")
@pytest.mark.asyncio
async def test_overlay_eval_uses_region_padding_on_ru_build() -> None:
    """End-to-end: the cta_button rule resolves the RU region's y_padding_ratio.

    The DSL `building.upgrade` rule carries no padding; the blue-button evaluator
    must fall back to the area.json region so the grey lower pill is skipped and
    the active upper button matches.
    """
    img = cv2.imread(str(FIXTURE))
    assert img is not None
    area_doc = load_area_doc(REPO_ROOT, game="wos_ru")

    def rule(region: str) -> dict:
        return {
            "name": f"dsl.test.{region}.visible",
            "region": region,
            "action": "cta_button",
            "color": "blue",
            "threshold": 0.5,
        }

    out = await evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule("upgrade_button"), rule("upgrade_button_top")]
    )

    lower = out["dsl.test.upgrade_button.visible"]
    assert lower.get("matched") is False, lower

    upper = out["dsl.test.upgrade_button_top.visible"]
    assert upper.get("matched") is True, upper
    px = round(float(upper["tap_x_pct"]) / 100.0 * 720)
    py = round(float(upper["tap_y_pct"]) / 100.0 * 1280)
    assert 540 <= px <= 660 and 497 <= py <= 572, (px, py)
