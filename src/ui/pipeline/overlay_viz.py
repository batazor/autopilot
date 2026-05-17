from __future__ import annotations

from typing import TYPE_CHECKING, Any

import cv2

from layout.area_lookup import screen_region_by_name

if TYPE_CHECKING:
    import numpy as np


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


_COLOR_SEARCH_ROI = (0, 200, 255)
_COLOR_MATCH_OK = (0, 220, 0)
_COLOR_MATCH_REJECTED = (0, 200, 255)
_COLOR_TAP = (0, 0, 255)
_COLOR_AREA_BBOX = (0, 165, 255)
_COLOR_REGION_LAYER = (220, 120, 0)
_COLOR_DETECTOR_RED_DOT = (0, 0, 220)
_COLOR_DETECTOR_TAB_ACTIVE = (0, 200, 0)
_COLOR_DETECTOR_WHITE_BORDER = (255, 255, 255)


def draw_search_roi(
    vis: np.ndarray,
    payload: dict[str, Any],
    area_doc: dict[str, Any],
    rule_search_name: str,
) -> None:
    hi, wi = vis.shape[:2]
    sr_nm = str(payload.get("search_region") or rule_search_name or "").strip()
    if not sr_nm:
        return
    pr = screen_region_by_name(area_doc, sr_nm)
    if not pr:
        return
    search_bbox = pr[1].get("bbox")
    if not isinstance(search_bbox, dict):
        return
    L, T, R, B = bbox_pct_to_px_rect(search_bbox, wi, hi)
    cv2.rectangle(vis, (L, T), (R, B), _COLOR_SEARCH_ROI, 1)


def draw_match_box(vis: np.ndarray, payload: dict[str, Any], logical: str = "") -> None:
    hi, wi = vis.shape[:2]
    tl = payload.get("top_left")
    tw = int(payload.get("template_w") or 0)
    th = int(payload.get("template_h") or 0)
    if not (isinstance(tl, (list, tuple)) and len(tl) >= 2 and tw > 0 and th > 0):
        return
    matched = bool(payload.get("matched"))
    x0 = int(float(tl[0]))
    y0 = int(float(tl[1]))
    x1, y1 = min(wi, x0 + tw), min(hi, y0 + th)
    box_col = _COLOR_MATCH_OK if matched else _COLOR_MATCH_REJECTED
    cv2.rectangle(vis, (x0, y0), (x1, y1), box_col, 2)
    cx, cy = x0 + tw // 2, y0 + th // 2
    cv2.circle(vis, (cx, cy), 5, box_col, 2)
    if logical:
        label = logical[:28] + (" ✓" if matched else " ✗")
        cv2.putText(
            vis,
            label,
            (x0 + 2, max(18, y0 - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            box_col,
            1,
            cv2.LINE_AA,
        )


def draw_tap_marker(vis: np.ndarray, payload: dict[str, Any]) -> None:
    hi, wi = vis.shape[:2]
    txp = payload.get("tap_x_pct")
    typ = payload.get("tap_y_pct")
    if txp is None or typ is None:
        return
    tx = max(0, min(wi - 1, int(float(txp) / 100.0 * wi)))
    ty = max(0, min(hi - 1, int(float(typ) / 100.0 * hi)))
    cv2.drawMarker(vis, (tx, ty), _COLOR_TAP, cv2.MARKER_CROSS, 18, 2)
    cv2.circle(vis, (tx, ty), 9, _COLOR_TAP, 2)


def draw_bbox_pct(
    vis: np.ndarray,
    bbox: dict[str, Any],
    *,
    color: tuple[int, int, int],
    thickness: int = 2,
    label: str = "",
) -> None:
    hi, wi = vis.shape[:2]
    L, T, R, B = bbox_pct_to_px_rect(bbox, wi, hi)
    cv2.rectangle(vis, (L, T), (R, B), color, thickness)
    if label:
        cv2.putText(
            vis,
            label[:24],
            (L + 2, max(14, T - 4)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            color,
            1,
            cv2.LINE_AA,
        )


def annotate_overlay_layers(
    image_bgr: np.ndarray,
    *,
    results: dict[str, Any],
    logical_names: list[str],
    area_doc: dict[str, Any],
    rule_search: dict[str, str],
    show_search_roi: bool = True,
    show_match_box: bool = True,
    show_tap: bool = True,
    show_area_bbox: bool = False,
    extra_region_bboxes: list[tuple[str, dict[str, Any]]] | None = None,
    detector_bboxes: list[tuple[str, dict[str, Any], tuple[int, int, int]]] | None = None,
) -> np.ndarray:
    """Selectively draw overlay debug layers on a copy of ``image_bgr``.

    ``extra_region_bboxes`` overlays additional area.json regions (name, bbox_pct).
    ``detector_bboxes`` overlays detector ROIs with custom colors.
    """
    vis = image_bgr.copy()
    for logical in logical_names:
        p = results.get(logical)
        if not isinstance(p, dict):
            continue
        if show_search_roi:
            draw_search_roi(vis, p, area_doc, rule_search.get(logical, ""))
        if show_match_box:
            draw_match_box(vis, p, logical)
        if show_tap:
            draw_tap_marker(vis, p)
        if show_area_bbox:
            reg_name = str(p.get("region") or "").strip()
            if reg_name:
                pr = screen_region_by_name(area_doc, reg_name)
                if pr and isinstance(pr[1].get("bbox"), dict):
                    draw_bbox_pct(
                        vis,
                        pr[1]["bbox"],
                        color=_COLOR_AREA_BBOX,
                        thickness=2,
                        label=reg_name,
                    )

    for name, bb in extra_region_bboxes or []:
        if isinstance(bb, dict):
            draw_bbox_pct(vis, bb, color=_COLOR_REGION_LAYER, thickness=2, label=name)

    for name, bb, color in detector_bboxes or []:
        if isinstance(bb, dict):
            draw_bbox_pct(vis, bb, color=color, thickness=2, label=name)

    return vis


def detector_color(kind: str) -> tuple[int, int, int]:
    k = (kind or "").strip().lower()
    if k == "red_dot":
        return _COLOR_DETECTOR_RED_DOT
    if k == "tab_active":
        return _COLOR_DETECTOR_TAB_ACTIVE
    if k == "white_border":
        return _COLOR_DETECTOR_WHITE_BORDER
    return _COLOR_REGION_LAYER


def annotate_overlay_debug(
    image_bgr: np.ndarray,
    results: dict[str, Any],
    logical_names: list[str],
    area_doc: dict[str, Any],
    rule_search: dict[str, str],
) -> np.ndarray:
    """Back-compat wrapper: all three classic layers (ROI, match, tap)."""
    return annotate_overlay_layers(
        image_bgr,
        results=results,
        logical_names=logical_names,
        area_doc=area_doc,
        rule_search=rule_search,
        show_search_roi=True,
        show_match_box=True,
        show_tap=True,
    )
