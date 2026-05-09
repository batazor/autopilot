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


def test_match_template_caps_high_ncc_with_color_difference() -> None:
    hi, wi = 160, 120
    frame = np.zeros((hi, wi, 3), dtype=np.uint8)

    template = np.zeros((90, 40, 3), dtype=np.uint8)
    template[:] = (150, 190, 210)
    template[:, 2:6] = (0, 220, 255)
    template[:, -6:-2] = (0, 220, 255)
    template[2:6, :] = (0, 220, 255)
    template[-6:-2, :] = (0, 220, 255)
    cv2_color_red = (0, 0, 255)
    template[35:55, 16:24] = cv2_color_red

    # Similar luminance gradient, but none of the red/yellow landmarks.
    false_patch = np.zeros_like(template)
    false_patch[:] = (130, 130, 130)
    false_patch[:, :20] = (170, 150, 130)
    false_patch[:, 20:] = (95, 120, 145)

    frame[20:110, 30:70] = false_patch
    bbox = {
        "x": 0.0,
        "y": 0.0,
        "width": 100.0,
        "height": 100.0,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }

    res = match_template_in_search_roi_bbox_percent(frame, template, bbox)

    assert res["score"] < 0.98
    assert res["score"] <= res["score_color"]


def test_match_template_rejects_gross_template_vs_primary_bbox_size() -> None:
    """Tiny template vs large labeled bbox must not slide-match inside a big ROI."""
    hi, wi = 100, 100
    frame = np.zeros((hi, wi, 3), dtype=np.uint8)
    primary_bbox = {
        "x": 0.0,
        "y": 0.0,
        "width": 100.0,
        "height": 100.0,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    tpl = np.zeros((8, 10, 3), dtype=np.uint8)
    search_bbox = primary_bbox.copy()
    with pytest.raises(ValueError, match="template PNG 10×8"):
        match_template_in_search_roi_bbox_percent(
            frame,
            tpl,
            search_bbox,
            primary_bbox_percent=primary_bbox,
        )


def test_match_template_small_primary_requires_exact_template_size() -> None:
    """Regions under ~20px max side require pixel-exact template dimensions."""
    hi, wi = 200, 200
    frame = np.zeros((hi, wi, 3), dtype=np.uint8)
    primary_bbox = {
        "x": 0.0,
        "y": 0.0,
        "width": 10.0,
        "height": 10.0,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    tpl = np.zeros((19, 19, 3), dtype=np.uint8)
    search_bbox = {
        "x": 0.0,
        "y": 0.0,
        "width": 100.0,
        "height": 100.0,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    with pytest.raises(ValueError, match="Small-region:.*template PNG"):
        match_template_in_search_roi_bbox_percent(
            frame,
            tpl,
            search_bbox,
            primary_bbox_percent=primary_bbox,
        )


def test_match_template_accepts_primary_within_10px_per_axis() -> None:
    hi, wi = 240, 320
    frame = np.zeros((hi, wi, 3), dtype=np.uint8)
    frame[:] = (40, 40, 40)
    th, tw = 48, 52
    tpl = np.zeros((th, tw, 3), dtype=np.uint8)
    tpl[:] = (200, 100, 50)
    x0, y0 = 120, 88
    frame[y0 : y0 + th, x0 : x0 + tw] = tpl

    pw, ph = 55, 50
    primary_bbox = {
        "x": 100.0 * x0 / wi,
        "y": 100.0 * y0 / hi,
        "width": 100.0 * pw / wi,
        "height": 100.0 * ph / hi,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    margin = 40
    search_bbox = {
        "x": 100.0 * x0 / wi,
        "y": 100.0 * y0 / hi,
        "width": 100.0 * (tw + margin) / wi,
        "height": 100.0 * (th + margin) / hi,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    res = match_template_in_search_roi_bbox_percent(
        frame,
        tpl,
        search_bbox,
        primary_bbox_percent=primary_bbox,
    )
    assert res["top_left"] == (x0, y0)
    assert res["score"] >= 0.99
