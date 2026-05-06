"""ROI sliding template match used when overlay ``search_region`` is set."""

from __future__ import annotations

import numpy as np
import pytest

from layout.template_match import match_template_in_search_roi_bbox_percent


def test_match_template_in_search_roi_finds_embedded_patch() -> None:
    hi, wi = 240, 320
    frame = np.zeros((hi, wi, 3), dtype=np.uint8)
    frame[:] = (40, 40, 40)

    tpl_hw = 24
    tpl = np.zeros((tpl_hw, tpl_hw, 3), dtype=np.uint8)
    tpl[:] = (200, 100, 50)

    y0, x0 = 88, 120
    frame[y0 : y0 + tpl_hw, x0 : x0 + tpl_hw] = tpl

    search_bbox = {
        "x": 100.0 * x0 / wi,
        "y": 100.0 * y0 / hi,
        "width": 100.0 * (tpl_hw + 40) / wi,
        "height": 100.0 * (tpl_hw + 40) / hi,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }

    res = match_template_in_search_roi_bbox_percent(frame, tpl, search_bbox)
    assert res["score"] >= 0.99
    assert res["top_left"] == (x0, y0)


def test_match_template_complains_when_template_larger_than_roi() -> None:
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    tpl = np.zeros((90, 90, 3), dtype=np.uint8)
    bbox = {
        "x": 40.0,
        "y": 40.0,
        "width": 20.0,
        "height": 20.0,
        "rotation": 0.0,
        "original_width": 100,
        "original_height": 100,
    }
    with pytest.raises(ValueError, match="must fit inside"):
        match_template_in_search_roi_bbox_percent(frame, tpl, bbox)
