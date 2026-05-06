from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from layout.area_lookup import screen_region_by_name


def region_area_action(area_doc: dict[str, Any], region_name: str) -> str:
    pair = screen_region_by_name(area_doc, str(region_name or "").strip())
    if pair is None:
        return ""
    return str(pair[1].get("action") or "")


def bbox_pct_to_px_rect(bb: dict[str, Any], wi: int, hi: int) -> tuple[int, int, int, int]:
    x = float(bb.get("x") or 0.0)
    y = float(bb.get("y") or 0.0)
    w = float(bb.get("width") or 0.0)
    h = float(bb.get("height") or 0.0)
    left = max(0, min(wi - 1, int(x / 100.0 * wi)))
    top = max(0, min(hi - 1, int(y / 100.0 * hi)))
    right = max(left + 1, min(wi, int((x + w) / 100.0 * wi)))
    bottom = max(top + 1, min(hi, int((y + h) / 100.0 * hi)))
    return left, top, right, bottom


def maybe_downscale_for_ui(image_bgr: np.ndarray, max_side: int = 960) -> np.ndarray:
    hi, wi = image_bgr.shape[:2]
    m = max(hi, wi)
    if m <= max_side:
        return image_bgr
    scale = max_side / float(m)
    return cv2.resize(
        image_bgr,
        (int(round(wi * scale)), int(round(hi * scale))),
        interpolation=cv2.INTER_AREA,
    )


def annotate_overlay_debug(
    image_bgr: np.ndarray,
    results: dict[str, Any],
    logical_names: list[str],
    area_doc: dict[str, Any],
    rule_search: dict[str, str],
) -> np.ndarray:
    vis = image_bgr.copy()
    hi, wi = vis.shape[:2]
    font = cv2.FONT_HERSHEY_SIMPLEX
    for logical in logical_names:
        p = results.get(logical)
        if not isinstance(p, dict):
            continue
        sr_nm = str(p.get("search_region") or rule_search.get(logical, "") or "").strip()
        if sr_nm:
            pr = screen_region_by_name(area_doc, sr_nm)
            if pr:
                reg_s = pr[1]
                search_bbox = reg_s.get("bbox")
                if isinstance(search_bbox, dict):
                    L, T, R, B = bbox_pct_to_px_rect(search_bbox, wi, hi)
                    cv2.rectangle(vis, (L, T), (R, B), (0, 200, 255), 1)

        tl = p.get("top_left")
        tw = int(p.get("template_w") or 0)
        th = int(p.get("template_h") or 0)
        matched = bool(p.get("matched"))
        if isinstance(tl, (list, tuple)) and len(tl) >= 2 and tw > 0 and th > 0:
            x0 = int(float(tl[0]))
            y0 = int(float(tl[1]))
            x1, y1 = min(wi, x0 + tw), min(hi, y0 + th)
            box_col = (0, 220, 0) if matched else (0, 200, 255)
            cv2.rectangle(vis, (x0, y0), (x1, y1), box_col, 2)
            cx, cy = x0 + tw // 2, y0 + th // 2
            cv2.circle(vis, (cx, cy), 5, box_col, 2)
            label = (logical[:28] + (" ✓" if matched else " ✗")) if logical else ""
            if label:
                cv2.putText(
                    vis,
                    label,
                    (x0 + 2, max(18, y0 - 4)),
                    font,
                    0.42,
                    box_col,
                    1,
                    cv2.LINE_AA,
                )

        txp = p.get("tap_x_pct")
        typ = p.get("tap_y_pct")
        if txp is not None and typ is not None:
            tx = int(float(txp) / 100.0 * wi)
            ty = int(float(typ) / 100.0 * hi)
            tx = max(0, min(wi - 1, tx))
            ty = max(0, min(hi - 1, ty))
            cv2.drawMarker(vis, (tx, ty), (0, 0, 255), cv2.MARKER_CROSS, 18, 2)
            cv2.circle(vis, (tx, ty), 9, (0, 0, 255), 2)
    return vis

