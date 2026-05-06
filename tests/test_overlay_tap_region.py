"""Optional ``tap_region`` overrides overlay tap coordinates."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from analysis.overlay import evaluate_overlay_rules
from layout.bbox_percent import bbox_percent_center_xy_pct


@pytest.fixture
def tap_overlay_repo(tmp_path: Path) -> tuple[Path, dict]:
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
                        "overlay_auxiliary": True,
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
            "action": "findIcon",
            "threshold": 0.99,
        }
    ]
    return repo, {"doc": doc, "rules": rules, "frame": frame, "bbox_tap": bbox_tap}


def test_tap_region_overrides_1to1_match_centre(tap_overlay_repo: tuple[Path, dict]) -> None:
    repo, bundle = tap_overlay_repo
    out = evaluate_overlay_rules(bundle["frame"], bundle["doc"], repo, bundle["rules"])
    row = out["t.visible"]
    assert row.get("matched") is True
    exp_tx, exp_ty = bbox_percent_center_xy_pct(bundle["bbox_tap"])
    assert pytest.approx(row["tap_x_pct"], rel=1e-9) == exp_tx
    assert pytest.approx(row["tap_y_pct"], rel=1e-9) == exp_ty
    assert row.get("tap_region") == "icon_tap"


def test_unknown_tap_region_fails_closed(tap_overlay_repo: tuple[Path, dict]) -> None:
    repo, bundle = tap_overlay_repo
    rules = [
        {
            "name": "t.visible",
            "region": "icon",
            "tap_region": "missing_tap",
            "action": "findIcon",
            "threshold": 0.99,
        }
    ]
    out = evaluate_overlay_rules(bundle["frame"], bundle["doc"], repo, rules)
    row = out["t.visible"]
    assert row.get("matched") is False
    assert row.get("reason") == "unknown_tap_region"
