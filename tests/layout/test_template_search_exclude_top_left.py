from __future__ import annotations

import numpy as np

from layout.template_match import match_template_in_search_roi_bbox_percent


def test_match_template_search_excludes_previous_top_left() -> None:
    """Hybrid NCC+pHash search skips an excluded top-left and finds the second copy."""
    hi, wi = 100, 100
    frame = np.full((hi, wi, 3), 128, dtype=np.uint8)
    th, tw = 10, 10
    tpl = np.zeros((th, tw, 3), dtype=np.uint8)
    tpl[:] = (200, 100, 50)
    tpl[::2, :] = (160, 80, 40)

    x1, y1 = 20, 25
    frame[y1 : y1 + th, x1 : x1 + tw] = tpl

    x2, y2 = 65, 70
    frame[y2 : y2 + th, x2 : x2 + tw] = tpl

    bbox = {
        "x": 0.0,
        "y": 0.0,
        "width": 100.0,
        "height": 100.0,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }

    res1 = match_template_in_search_roi_bbox_percent(frame, tpl, bbox)
    assert float(res1["score"]) >= 0.9
    tl1 = tuple(res1["top_left"])

    res2 = match_template_in_search_roi_bbox_percent(
        frame,
        tpl,
        bbox,
        exclude_top_lefts=[tl1],
        exclude_radius_px=12,
    )
    assert res2["top_left"] != tl1
    assert abs(int(res2["top_left"][0]) - x2) <= 2
    assert abs(int(res2["top_left"][1]) - y2) <= 2
    assert float(res2["score"]) >= 0.9
