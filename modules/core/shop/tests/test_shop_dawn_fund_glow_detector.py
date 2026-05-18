"""Detect which dawn_fund prize tile is claimable via the yellow rim glow.

The dawn_fund page stacks 4 diamond rewards in a vertical column inside
``shop.to.dawn_fund.box``. Locked tiles render with a flat purple body;
the next claimable tile gets a saturated yellow-to-orange rim around it.

Two contracts:

* On the reference frame, exactly tile 0 (the 100-diamond reward) carries
  the glow — the others are locked. Catches regressions in the HSV gates
  that would either over-detect (locked tiles glowing) or miss the glow
  altogether.
* The boolean ``has_yellow_glow_in_bbox_percent`` on the full box returns
  True when any slot is claimable — used by analyze rules to decide
  whether to even push the dawn_fund claim scenario.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import pytest

from layout.area_manifest import load_area_doc
from layout.yellow_glow_detector import (
    find_glowing_slots_in_grid,
    find_yellow_glow_squares,
    has_yellow_glow_in_bbox_percent,
)

if TYPE_CHECKING:
    import numpy as np

MODULE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = MODULE_DIR.parents[2]
REFERENCES_DIR = MODULE_DIR / "references"
BOX_REGION = "shop.to.dawn_fund.box"
N_SLOTS = 4


def _load_bgr(name: str) -> np.ndarray:
    path = REFERENCES_DIR / name
    frame = cv2.imread(str(path))
    assert frame is not None, f"failed to load reference: {path}"
    return frame


@pytest.fixture(scope="module")
def box_bbox() -> dict:
    area_doc = load_area_doc(REPO_ROOT)
    for screen in area_doc.get("screens", []):
        for region in screen.get("regions", []):
            if region.get("name") == BOX_REGION:
                return region["bbox"]
    pytest.fail(f"{BOX_REGION!r} not in area doc")


def test_exactly_first_slot_is_claimable(box_bbox: dict) -> None:
    """On the dawn_fund reference, tile 0 has the yellow rim; 1–3 are locked."""
    frame = _load_bgr("page.shop.dawn_fund.png")
    slots = find_glowing_slots_in_grid(frame, box_bbox, n_slots=N_SLOTS)
    claimable_indices = [s.index for s in slots if s.is_claimable]
    assert claimable_indices == [0], (
        f"expected only tile 0 claimable, got {claimable_indices} "
        f"(ratios: {[(s.index, round(s.glow_ratio, 4)) for s in slots]})"
    )


def test_glow_ratio_gap_is_wide(box_bbox: dict) -> None:
    """Claimable tile must out-glow locked tiles by a 5× margin.

    Threshold drift in either direction (over-sensitive HSV or saturation
    floor too low) erodes the gap and silently mis-routes the bot. The 5×
    floor isn't tight — on the current ref claimable measures ~0.042 and
    locked tiles ~0.000, so the real margin is essentially infinite. The
    check exists to catch a future regression that pushes locked tiles up.
    """
    frame = _load_bgr("page.shop.dawn_fund.png")
    slots = find_glowing_slots_in_grid(frame, box_bbox, n_slots=N_SLOTS)
    claimable = max(slots, key=lambda s: s.glow_ratio)
    locked = [s for s in slots if s.index != claimable.index]
    max_locked = max(s.glow_ratio for s in locked) if locked else 0.0
    # If max_locked is ~0, the ratio comparison would div-by-zero — guard it.
    if max_locked > 0:
        ratio = claimable.glow_ratio / max_locked
        assert ratio >= 5.0, (
            f"claimable/locked glow ratio={ratio:.1f}× is below the 5× floor "
            f"(claimable={claimable.glow_ratio:.4f}, max_locked={max_locked:.4f})"
        )


def test_box_level_boolean_fires_when_any_slot_claimable(box_bbox: dict) -> None:
    """The high-level bool returns True when any slot in the box glows."""
    frame = _load_bgr("page.shop.dawn_fund.png")
    assert has_yellow_glow_in_bbox_percent(frame, box_bbox) is True


def test_full_image_scan_finds_three_claim_tiles() -> None:
    """Whole-frame scan without bbox returns exactly the 3 claim tiles.

    The dawn_fund grid has three columns of rewards (Free, Mid, Epic) and
    the top row of each is currently claimable. All three tiles share the
    same ~92×92 footprint; locked siblings below them either show a flat
    purple body (Free/Epic side columns — hollow yellow rim discriminates)
    or a filled warm body (Mid column — bright cream border discriminates
    from the locked tiles' darker saturated orange).

    The size band intentionally excludes the larger decorative
    "Claimable" indicator chest in the page header (~150 px). That one
    isn't a tap target — it's just iconography next to the page title."""
    frame = _load_bgr("page.shop.dawn_fund.png")
    squares = find_yellow_glow_squares(frame)

    assert len(squares) == 3, (
        f"expected 3 claim tiles, got {len(squares)}: "
        f"{[(round(s.bbox_percent['x'], 1), round(s.bbox_percent['y'], 1), round(s.fill_ratio, 2)) for s in squares]}"
    )

    centers = {
        (
            round(s.bbox_percent["x"] + s.bbox_percent["width"] / 2),
            round(s.bbox_percent["y"] + s.bbox_percent["height"] / 2),
        )
        for s in squares
    }
    # All three tiles sit on the same row (~y=59 %), one per column.
    # Centers grouped to ±2 % tolerance (closed-CC bbox can wobble a pixel).
    expected_zones = [
        (31, 59),  # Free 100  — left column, hollow rim
        (61, 59),  # Mid 1000  — center column, cream border on warm body
        (80, 59),  # Epic 100  — right column, hollow rim
    ]
    for ex, ey in expected_zones:
        assert any(abs(cx - ex) <= 2 and abs(cy - ey) <= 2 for cx, cy in centers), (
            f"expected claim tile near ({ex}%, {ey}%) not found in {centers}"
        )


def test_slot_bboxes_partition_the_box_column(box_bbox: dict) -> None:
    """Slot bboxes share the box's X span and tile the Y span without gaps."""
    frame = _load_bgr("page.shop.dawn_fund.png")
    slots = find_glowing_slots_in_grid(frame, box_bbox, n_slots=N_SLOTS)
    assert len(slots) == N_SLOTS
    for s in slots:
        assert s.bbox_percent["x"] == box_bbox["x"]
        assert s.bbox_percent["width"] == box_bbox["width"]
    # Y stripes meet end-to-end.
    for prev, curr in zip(slots, slots[1:], strict=False):
        assert abs(
            (prev.bbox_percent["y"] + prev.bbox_percent["height"])
            - curr.bbox_percent["y"]
        ) < 1e-6
