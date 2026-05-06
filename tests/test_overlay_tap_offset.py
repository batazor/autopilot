"""``tap_offset_from_match``: tap follows template match plus labeled delta."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from analysis.overlay import centers_delta_pct_between_regions, evaluate_overlay_rules


def test_centers_delta_between_regions() -> None:
    doc = {
        "screens": [
            {
                "ocr": "references/x.png",
                "regions": [
                    {
                        "name": "a",
                        "bbox": {
                            "x": 10.0,
                            "y": 10.0,
                            "width": 10.0,
                            "height": 10.0,
                            "rotation": 0.0,
                            "original_width": 100,
                            "original_height": 100,
                        },
                    },
                    {
                        "name": "b",
                        "bbox": {
                            "x": 30.0,
                            "y": 10.0,
                            "width": 10.0,
                            "height": 10.0,
                            "rotation": 0.0,
                            "original_width": 100,
                            "original_height": 100,
                        },
                    },
                ],
            }
        ]
    }
    d = centers_delta_pct_between_regions(doc, "a", "b")
    assert d is not None
    assert pytest.approx(d[0], rel=1e-9) == 20.0
    assert pytest.approx(d[1], rel=1e-9) == 0.0


@pytest.fixture
def offset_repo(tmp_path: Path) -> tuple[Path, dict]:
    repo = tmp_path
    ref_rel = "references/ref.png"
    (repo / "references/crop").mkdir(parents=True)

    hi, wi = 100, 100
    frame = np.zeros((hi, wi, 3), dtype=np.uint8)
    frame[:] = (40, 40, 40)
    tpl_s = 12
    tpl = np.full((tpl_s, tpl_s, 3), (200, 30, 30), dtype=np.uint8)
    y0, x0 = 8, 8
    frame[y0 : y0 + tpl_s, x0 : x0 + tpl_s] = tpl

    stem = Path(ref_rel).stem
    crop_path = repo / "references/crop" / f"{stem}_icon.png"
    assert cv2.imwrite(str(crop_path), tpl)

    bbox_icon = {
        "x": 100.0 * x0 / wi,
        "y": 100.0 * y0 / hi,
        "width": 100.0 * tpl_s / wi,
        "height": 100.0 * tpl_s / hi,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    bbox_tap = {
        "x": 80.0,
        "y": 80.0,
        "width": 10.0,
        "height": 10.0,
        "rotation": 0.0,
        "original_width": wi,
        "original_height": hi,
    }
    doc = {
        "version": 2,
        "fsm": {"initial_screen": "", "transitions": []},
        "screens": [
            {
                "id": 1,
                "screen_id": "",
                "ocr": ref_rel,
                "regions": [
                    {
                        "name": "icon",
                        "action": "exist",
                        "type": "string",
                        "threshold": 0.9,
                        "bbox": bbox_icon,
                    },
                    {
                        "name": "icon_tap",
                        "action": "exist",
                        "type": "string",
                        "threshold": 0.9,
                        "bbox": bbox_tap,
                    },
                ],
            }
        ],
    }
    rules = [
        {
            "name": "t.visible",
            "region": "icon",
            "tap_region": "icon_tap",
            "tap_offset_from_match": True,
            "action": "findIcon",
            "threshold": 0.99,
        }
    ]
    return repo, {"doc": doc, "rules": rules, "frame": frame, "bbox_icon": bbox_icon}


def test_tap_offset_follows_match_not_fixed_tap_bbox(offset_repo: tuple[Path, dict]) -> None:
    repo, bundle = offset_repo
    out = evaluate_overlay_rules(bundle["frame"], bundle["doc"], repo, bundle["rules"])
    row = out["t.visible"]
    assert row.get("matched") is True
    dd = centers_delta_pct_between_regions(bundle["doc"], "icon", "icon_tap")
    assert dd is not None
    mx = row["tap_match_x_pct"]
    my = row["tap_match_y_pct"]
    assert pytest.approx(row["tap_x_pct"], rel=1e-6) == mx + dd[0]
    assert pytest.approx(row["tap_y_pct"], rel=1e-6) == my + dd[1]
