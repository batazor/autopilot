from __future__ import annotations

import numpy as np

from analysis.overlay_red_dot_gate import _finalize_findicon_hit


def test_findicon_min_patch_bright_ratio_rejects_dark_patch_after_match() -> None:
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    template = np.zeros((10, 10, 3), dtype=np.uint8)
    template[0, :] = (245, 245, 245)

    hit = _finalize_findicon_hit(
        image_bgr=image,
        template_bgr=template,
        res={"top_left": (5, 5), "score_ncc": 0.95},
        matched=True,
        score=0.95,
        threshold=0.9,
        template_w=10,
        template_h=10,
        rule={},
        min_sat=None,
        min_patch_bright_ratio=0.05,
        region_name="button.test",
        resolved_region_name="button.test",
        resolved_version=None,
        match_x_pct=50.0,
        match_y_pct=50.0,
        tap_delta=None,
        push_tasks=[],
        set_node_s="",
        priority=None,
    )

    assert hit["matched"] is False
    assert hit["reason"] == "low_patch_bright_ratio"
    assert hit["template_bright_ratio"] == 0.1
    assert hit["patch_bright_ratio"] == 0.0


def test_findicon_min_patch_bright_ratio_accepts_bright_patch_after_match() -> None:
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    image[5, 5:15] = (245, 245, 245)
    template = np.zeros((10, 10, 3), dtype=np.uint8)
    template[0, :] = (245, 245, 245)

    hit = _finalize_findicon_hit(
        image_bgr=image,
        template_bgr=template,
        res={"top_left": (5, 5), "score_ncc": 0.95},
        matched=True,
        score=0.95,
        threshold=0.9,
        template_w=10,
        template_h=10,
        rule={},
        min_sat=None,
        min_patch_bright_ratio=0.05,
        region_name="button.test",
        resolved_region_name="button.test",
        resolved_version=None,
        match_x_pct=50.0,
        match_y_pct=50.0,
        tap_delta=None,
        push_tasks=[],
        set_node_s="",
        priority=None,
    )

    assert hit["matched"] is True
    assert hit["patch_bright_ratio"] == 0.1
