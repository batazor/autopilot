"""Overlay rules with ``isWhiteBorder: true|false`` (programmatic halo detection).

Mirrors :mod:`test_overlay_red_dot_rule` — exercises the overlay-engine branch
that turns an ``isWhiteBorder:`` flag on an overlay rule into an action match
against the live rolling PNG. DSL-level coverage lives in
:mod:`test_dsl_is_white_border`; the detector itself is covered by
:mod:`test_white_border_detector`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from analysis.overlay_engine import evaluate_overlay_rules_async

REPO_ROOT = Path(__file__).resolve().parents[1]


def _frame_with_white_halo(w: int = 200, h: int = 200, *, with_halo: bool) -> np.ndarray:
    """200×200 synthetic frame holding a colored icon body in the center.

    Matches the helper in ``test_dsl_is_white_border.py`` so this overlay-rule
    coverage exercises the same shape of evidence the DSL builder uses.
    """
    if with_halo:
        bg = (245, 245, 245)
    else:
        bg = (220, 200, 50)  # BGR — bright cyan, fails the saturation gate
    img = np.full((h, w, 3), bg, dtype=np.uint8)
    cv2.rectangle(img, (70, 70), (130, 130), (160, 50, 130), thickness=-1)
    return img


def _area_doc_with_region(name: str = "reward.tile") -> dict[str, Any]:
    return {
        "screens": [
            {
                "name": "rewards",
                "regions": [
                    {
                        "name": name,
                        "bbox": {"x": 35.0, "y": 35.0, "width": 30.0, "height": 30.0},
                    }
                ],
            }
        ]
    }


@pytest.mark.asyncio
async def test_isworkerwhite_border_matches_when_halo_present() -> None:
    img = _frame_with_white_halo(with_halo=True)
    area_doc = _area_doc_with_region()
    rule = {
        "name": "reward.highlighted",
        "region": "reward.tile",
        "isWhiteBorder": True,
        "screens": ["rewards"],
    }
    out = await evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], current_screen="rewards"
    )

    row = out.get("reward.highlighted")
    assert isinstance(row, dict)
    assert row.get("action") == "white_border"
    assert row.get("matched") is True, row
    assert row.get("white_border_present") is True
    # Tap defaults to bbox center for the follow-up click step.
    assert row.get("tap_x_pct") == pytest.approx(35.0 + 30.0 / 2.0)
    assert row.get("tap_y_pct") == pytest.approx(35.0 + 30.0 / 2.0)
    # Halo stats are surfaced for UI threshold tuning.
    for key in (
        "halo_saturation",
        "halo_value",
        "interior_saturation",
        "interior_saturation_excess",
        "ring_count",
        "max_mean_saturation",
        "min_mean_value",
    ):
        assert key in row, key


@pytest.mark.asyncio
async def test_white_border_no_match_when_halo_absent() -> None:
    img = _frame_with_white_halo(with_halo=False)
    area_doc = _area_doc_with_region()
    rule = {
        "name": "reward.highlighted",
        "region": "reward.tile",
        "isWhiteBorder": True,
        "screens": ["rewards"],
    }
    out = await evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], current_screen="rewards"
    )

    row = out.get("reward.highlighted")
    assert isinstance(row, dict)
    assert row.get("matched") is False, row
    assert row.get("white_border_present") is False


@pytest.mark.asyncio
async def test_iswhiteborder_false_matches_when_halo_absent() -> None:
    img = _frame_with_white_halo(with_halo=False)
    area_doc = _area_doc_with_region()
    rule = {
        "name": "reward.unhighlighted",
        "region": "reward.tile",
        "isWhiteBorder": False,
        "screens": ["rewards"],
    }
    out = await evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], current_screen="rewards"
    )

    row = out.get("reward.unhighlighted")
    assert isinstance(row, dict)
    assert row.get("matched") is True, row
    assert row.get("want_white_border") is False
    assert row.get("white_border_present") is False


@pytest.mark.asyncio
async def test_white_border_missing_bbox_returns_typed_reason() -> None:
    img = _frame_with_white_halo(with_halo=True)
    area_doc = {
        "screens": [
            {
                "name": "rewards",
                "regions": [{"name": "reward.tile"}],  # no bbox
            }
        ]
    }
    rule = {
        "name": "reward.highlighted",
        "region": "reward.tile",
        "isWhiteBorder": True,
        "screens": ["rewards"],
    }
    out = await evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], current_screen="rewards"
    )

    row = out.get("reward.highlighted")
    assert isinstance(row, dict)
    assert row.get("matched") is False
    assert row.get("reason") == "missing_bbox"
    assert row.get("action") == "white_border"


@pytest.mark.asyncio
async def test_white_border_unknown_region_returns_typed_reason() -> None:
    img = _frame_with_white_halo(with_halo=True)
    area_doc = _area_doc_with_region()
    rule = {
        "name": "reward.highlighted",
        "region": "does.not.exist",
        "isWhiteBorder": True,
        "screens": ["rewards"],
    }
    out = await evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], current_screen="rewards"
    )

    row = out.get("reward.highlighted")
    assert isinstance(row, dict)
    assert row.get("matched") is False
    assert row.get("reason") == "unknown_region"
    assert row.get("action") == "white_border"


@pytest.mark.asyncio
async def test_white_border_per_rule_overrides_apply() -> None:
    """Per-rule ``max_mean_saturation`` tightens the halo gate, mirroring the
    tab_active path. Halo S ≈ 0 on a pure-white background, so setting the
    cap to a negative number forces the detector to reject."""
    img = _frame_with_white_halo(with_halo=True)
    area_doc = _area_doc_with_region()
    rule = {
        "name": "reward.highlighted",
        "region": "reward.tile",
        "isWhiteBorder": True,
        "screens": ["rewards"],
        "max_mean_saturation": -1.0,
    }
    out = await evaluate_overlay_rules_async(
        img, area_doc, REPO_ROOT, [rule], current_screen="rewards"
    )

    row = out.get("reward.highlighted")
    assert isinstance(row, dict)
    assert row.get("matched") is False
    assert row.get("white_border_present") is False
    assert row.get("max_mean_saturation") == -1.0
