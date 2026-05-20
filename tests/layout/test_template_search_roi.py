"""ROI sliding template match used when overlay ``search_region`` is set."""

from __future__ import annotations

import numpy as np
import pytest

from layout.template_match import (
    _phash_match_score,
    match_template_in_search_roi_bbox_percent,
    patch_bgr_from_bbox_percent,
)


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
    assert res["score"] >= 0.95
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


def _structured_color_template() -> np.ndarray:
    """Template with borders + red block (legacy color_difference fixture shape)."""
    template = np.zeros((90, 40, 3), dtype=np.uint8)
    template[:] = (150, 190, 210)
    template[:, 2:6] = (0, 220, 255)
    template[:, -6:-2] = (0, 220, 255)
    template[2:6, :] = (0, 220, 255)
    template[-6:-2, :] = (0, 220, 255)
    template[35:55, 16:24] = (0, 0, 255)
    return template


def test_match_template_phash_rejects_gradient_blob_without_template() -> None:
    """pHash: ROI with only a smooth gradient blob (no real template) stays below match threshold."""
    hi, wi = 160, 120
    template = _structured_color_template()

    false_patch = np.zeros_like(template)
    false_patch[:] = (130, 130, 130)
    false_patch[:, :20] = (170, 150, 130)
    false_patch[:, 20:] = (95, 120, 145)

    frame = np.full((hi, wi, 3), 40, dtype=np.uint8)
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

    assert float(res["score"]) < 0.95
    score_direct, hamming = _phash_match_score(false_patch, template)
    assert hamming > 0
    assert score_direct < 0.95


def test_match_template_phash_prefers_embedded_template_over_gradient_blob() -> None:
    """pHash scan picks the real embed; direct score for template beats color-gradient decoy."""
    hi, wi = 160, 120
    template = _structured_color_template()

    false_patch = np.zeros_like(template)
    false_patch[:] = (130, 130, 130)
    false_patch[:, :20] = (170, 150, 130)
    false_patch[:, 20:] = (95, 120, 145)

    th, tw = int(template.shape[0]), int(template.shape[1])
    x_real, y_real = 8, 10
    # Non-overlapping decoy (earlier fixture used 30,20 which clipped into the real embed).
    x_decoy, y_decoy = 55, 10

    frame = np.full((hi, wi, 3), 40, dtype=np.uint8)
    frame[y_real : y_real + th, x_real : x_real + tw] = template
    frame[y_decoy : y_decoy + th, x_decoy : x_decoy + tw] = false_patch

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

    assert res["top_left"] == (x_real, y_real)
    assert float(res["score"]) >= 0.99

    real_patch = frame[y_real : y_real + th, x_real : x_real + tw]
    assert np.array_equal(real_patch, template)

    score_identity, _ = _phash_match_score(template, template)
    score_embed, _ = _phash_match_score(real_patch, template)
    score_decoy, _ = _phash_match_score(false_patch, template)
    assert score_identity == score_embed == 1.0
    assert score_embed > score_decoy


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
    with pytest.raises(ValueError, match=r"Small-region:.*template PNG"):
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
    assert res["score"] >= 0.95


def test_sliding_search_phash_prefers_matching_bgr_over_duplicate_wrong_tint() -> None:
    """pHash scan prefers the true-colored embed over a same-layout grey duplicate."""
    hi, wi = 140, 180
    frame = np.full((hi, wi, 3), 45, dtype=np.uint8)
    th, tw = 28, 32
    tpl = np.zeros((th, tw, 3), dtype=np.uint8)
    tpl[:] = (230, 130, 55)
    tpl[8 : th - 8, 8 : tw - 8] = (240, 245, 250)
    tpl[4:6, :] = (255, 255, 255)
    tpl[-6:-4, :] = (255, 255, 255)

    wrong_tint = tpl.copy()
    wrong_tint[:] = (95, 95, 95)
    wrong_tint[8 : th - 8, 8 : tw - 8] = (115, 115, 115)

    # Upper placement: same silhouette as ``tpl`` but muted grey — often wins raw TM(NCC) first.
    xa, ya = 22, 18
    frame[ya : ya + th, xa : xa + tw] = wrong_tint
    xb, yb = 22, 78
    frame[yb : yb + th, xb : xb + tw] = tpl

    search_bbox = {
        "x": 100.0 * 10 / wi,
        "y": 100.0 * 10 / hi,
        "width": 100.0 * 160 / wi,
        "height": 100.0 * 120 / hi,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }

    res = match_template_in_search_roi_bbox_percent(frame, tpl, search_bbox)
    assert res["top_left"] == (xb, yb)
    assert float(res["score"]) >= 0.9
    score_grey, _ = _phash_match_score(wrong_tint, tpl)
    score_real, _ = _phash_match_score(tpl, tpl)
    assert score_real >= score_grey


def test_sliding_search_finds_patch_outside_primary_bbox_with_primary_dims_gate() -> None:
    """Same semantics as overlay ``findIcon`` + implicit ``*_search``: template size comes from the
    labeled primary bbox, but the live icon may appear anywhere inside the larger search ROI (not
    under the static primary rectangle).
    """
    hi, wi = 240, 320
    frame = np.full((hi, wi, 3), 55, dtype=np.uint8)

    primary_bbox = {
        "x": 8.0,
        "y": 8.0,
        "width": 14.0,
        "height": 11.0,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    primary_patch, (_pl, _pt) = patch_bgr_from_bbox_percent(frame, primary_bbox)
    th, tw = int(primary_patch.shape[0]), int(primary_patch.shape[1])

    tpl = np.zeros((th, tw, 3), dtype=np.uint8)
    tpl[:] = (40, 180, 240)
    tpl[2 : th - 2, 2 : tw - 2] = (20, 90, 200)
    tpl[1:4, :] = (255, 255, 255)
    tpl[-4:-1, :] = (255, 255, 255)

    x0, y0 = 210 - tw, 155 - th
    frame[y0 : y0 + th, x0 : x0 + tw] = tpl

    search_bbox = {
        "x": 45.0,
        "y": 38.0,
        "width": 52.0,
        "height": 58.0,
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
    assert res["score"] >= 0.95
    assert res["top_left"] == (x0, y0)

    wrong_patch, _ = patch_bgr_from_bbox_percent(frame, primary_bbox)
    wrong_score = float(np.mean(np.abs(wrong_patch.astype(np.float32) - tpl.astype(np.float32))))
    assert wrong_score > 30.0
