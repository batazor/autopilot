import json
from pathlib import Path
from typing import Any

import cv2

from layout.color_bucket import dominant_color_label_bgr


def _region_bbox_pct(area_doc: dict[str, Any], name: str) -> dict[str, Any]:
    for screen in area_doc.get("screens") or []:
        if not isinstance(screen, dict):
            continue
        regs = screen.get("regions") or []
        if not isinstance(regs, list):
            continue
        for reg in regs:
            if not isinstance(reg, dict):
                continue
            if str(reg.get("name") or "").strip() == name:
                bbox = reg.get("bbox")
                if isinstance(bbox, dict):
                    return bbox
    raise AssertionError(f"region bbox not found in area.json: {name}")


def test_color_check_is_has_new_mail_is_red() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    area_doc = json.loads((repo_root / "area.json").read_text(encoding="utf-8"))
    bbox = _region_bbox_pct(area_doc, "is_has_new_mail")

    img_path = repo_root / "tests" / "fixtures" / "main_city_current_state.png"
    img = cv2.imread(str(img_path))
    assert img is not None, f"failed to load fixture: {img_path}"

    h, w = int(img.shape[0]), int(img.shape[1])
    x0 = int(round(float(bbox["x"]) / 100.0 * w))
    y0 = int(round(float(bbox["y"]) / 100.0 * h))
    bw = int(round(float(bbox["width"]) / 100.0 * w))
    bh = int(round(float(bbox["height"]) / 100.0 * h))

    # Very small indicator; pad a bit to capture enough pixels.
    pad = 3
    x0 = max(0, min(w - 1, x0 - pad))
    y0 = max(0, min(h - 1, y0 - pad))
    x1 = max(x0 + 1, min(w, x0 + max(1, bw) + 2 * pad))
    y1 = max(y0 + 1, min(h, y0 + max(1, bh) + 2 * pad))

    patch = img[y0:y1, x0:x1]
    dominant, shares = dominant_color_label_bgr(patch)
    assert dominant == "red", f"dominant={dominant} shares={shares}"

