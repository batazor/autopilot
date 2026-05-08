from __future__ import annotations

from typing import Any

import numpy as np

from layout.template_match import match_template_in_search_roi_bbox_percent


def test_match_template_search_excludes_previous_top_left(monkeypatch: Any) -> None:
    # Create a fake "heatmap" with two peaks: one at (x=2,y=3) and another at (7,9).
    heat = np.full((12, 12), -1.0, dtype=np.float32)
    heat[3, 2] = 0.95
    heat[9, 7] = 0.90

    def _fake_match_template(_rg: Any, _tg: Any, _method: Any) -> Any:
        return heat.copy()

    monkeypatch.setattr("cv2.matchTemplate", _fake_match_template, raising=False)

    # Minimal images (content irrelevant due to monkeypatch).
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    tpl = np.zeros((10, 10, 3), dtype=np.uint8)
    # ROI at (0,0) for simplicity.
    bbox = {"x": 0.0, "y": 0.0, "width": 100.0, "height": 100.0}

    res1 = match_template_in_search_roi_bbox_percent(img, tpl, bbox)
    assert res1["top_left"] == (2, 3)

    res2 = match_template_in_search_roi_bbox_percent(
        img, tpl, bbox, exclude_top_lefts=[(2, 3)], exclude_radius_px=2
    )
    assert res2["top_left"] == (7, 9)

