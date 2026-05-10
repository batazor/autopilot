"""Compare an exported crop with the **same bbox rectangle** on another frame (1:1, no search).

Uses the same pixel rounding as :func:`ui.area_annotator.crop_region` / crop export.
The live frame must be the **same resolution** as when the crop was produced.

Sliding-window search (:func:`match_template_in_search_roi_bbox_percent`) can optionally require the
template pixel size to agree with the **primary** labeled bbox on the live frame (see
``primary_bbox_percent``). That blocks misleading TM peaks when a tiny template slides inside a
large ROI (e.g. ``10×8`` template vs ``157×74`` expected landmark).
"""

from __future__ import annotations

import math
from typing import TypedDict

import cv2
import numpy as np

# Live bbox patch vs exported template (sliding search): reject gross size mismatch.
_MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX = 10
# If either side is under this (max of W/H), require exact pixel dimensions (strict 1:1).
_SMALL_TEMPLATE_PRIMARY_MAX_SIDE_PX = 20


class TemplateMatchResult(TypedDict, total=False):
    # Conservative score: structural NCC capped by BGR color similarity.
    score: float
    # Global top-left (x, y); crop rounding matches labeling export.
    top_left: tuple[int, int]
    # Raw grayscale TM_CCOEFF_NORMED score before the color-similarity cap.
    score_ncc: float
    # Mean absolute BGR similarity: 1.0 is identical, 0.0 is maximally different.
    score_color: float
    # Edge-map similarity (Canny on grayscale) as a strict content check.
    score_edge: float
    # Second-best NCC peak in the search ROI, masked away from the winner by
    # at least ``template_w x template_h`` so it picks a structurally different
    # location. ``None`` for 1:1 matches (no sliding) or when the heatmap is too
    # small for a 2nd peak. Used by the peak-uniqueness gate to reject low-info
    # templates that produce a plateau of equally good candidates.
    score_ncc_second: float | None


def _color_similarity_score(patch_bgr: np.ndarray, template_bgr: np.ndarray) -> float:
    """Return a strict per-pixel BGR similarity score in ``[0, 1]``.

    Grayscale normalized correlation can be very high for the wrong UI patch when gradients line up.
    Capping it with color similarity rejects matches that miss saturated landmarks (red crosshair,
    yellow border, blue button, etc.).
    """
    if patch_bgr.shape != template_bgr.shape:
        raise ValueError(
            f"Color score shape mismatch: patch {patch_bgr.shape} vs template {template_bgr.shape}."
        )
    diff = np.abs(patch_bgr.astype(np.float32) - template_bgr.astype(np.float32))
    mae = float(np.mean(diff))
    return max(0.0, min(1.0, 1.0 - mae / 255.0))


def _edge_similarity_score(patch_bgr: np.ndarray, template_bgr: np.ndarray) -> float:
    """Return a strict edge-map similarity score in ``[0, 1]``.

    UI gradients (green bars, panels) can correlate well in grayscale and even in mean color.
    Edges from glyphs ("Claim") and borders are far more discriminative than flat fills.
    """
    if patch_bgr.shape != template_bgr.shape:
        raise ValueError(
            f"Edge score shape mismatch: patch {patch_bgr.shape} vs template {template_bgr.shape}."
        )
    pg = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    pe = cv2.Canny(pg, 50, 150)
    te = cv2.Canny(tg, 50, 150)
    diff = np.abs(pe.astype(np.float32) - te.astype(np.float32))
    mae = float(np.mean(diff))
    return max(0.0, min(1.0, 1.0 - mae / 255.0))


def _combined_match_score(
    patch_bgr: np.ndarray,
    template_bgr: np.ndarray,
) -> tuple[float, float, float, float]:
    pg = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    score_ncc = float(cv2.matchTemplate(pg, tg, cv2.TM_CCOEFF_NORMED)[0, 0])
    score_color = _color_similarity_score(patch_bgr, template_bgr)
    score_edge = _edge_similarity_score(patch_bgr, template_bgr)
    return min(score_ncc, score_color, score_edge), score_ncc, score_color, score_edge


def patch_bgr_from_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
) -> tuple[np.ndarray, tuple[int, int]]:
    """Cut out the bbox rectangle in pixels (percent of frame); mirrors labeling crop rounding."""
    if image_bgr.ndim != 3:
        raise ValueError("Expected HxWx3 BGR image.")
    hi, wi = image_bgr.shape[:2]

    left = bbox_percent["x"] / 100.0 * wi
    top = bbox_percent["y"] / 100.0 * hi
    width = bbox_percent["width"] / 100.0 * wi
    height = bbox_percent["height"] / 100.0 * hi

    L = int(math.floor(left))
    T = int(math.floor(top))
    R = int(math.ceil(left + width))
    B = int(math.ceil(top + height))

    L = max(0, min(L, wi - 1))
    T = max(0, min(T, hi - 1))
    R = max(L + 1, min(R, wi))
    B = max(T + 1, min(B, hi))

    return image_bgr[T:B, L:R].copy(), (L, T)


def validate_live_bbox_patch_vs_reference_dims(
    live_pw: int,
    live_ph: int,
    ref_pw: int,
    ref_ph: int,
    *,
    reference_label: str,
) -> None:
    """Reject gross mismatch between a live bbox cutout and a labeled reference tile (PNG).

    Same thresholds as sliding ``findIcon`` vs primary bbox: small regions (max side
    ``< _SMALL_TEMPLATE_PRIMARY_MAX_SIDE_PX``) require pixel-identical width/height; otherwise at
    most ``_MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX`` difference per axis.

    Used for ``color_check`` vs ``references/crop/…`` and internally for template validation.
    """
    small_region = (
        max(live_pw, live_ph) < _SMALL_TEMPLATE_PRIMARY_MAX_SIDE_PX
        or max(ref_pw, ref_ph) < _SMALL_TEMPLATE_PRIMARY_MAX_SIDE_PX
    )
    if small_region:
        if live_pw != ref_pw or live_ph != ref_ph:
            raise ValueError(
                f"Small-region: live bbox patch {live_pw}×{live_ph} must match {reference_label} "
                f"{ref_pw}×{ref_ph} exactly (1:1)."
            )
        return
    if (
        abs(live_pw - ref_pw) > _MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX
        or abs(live_ph - ref_ph) > _MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX
    ):
        raise ValueError(
            f"Live bbox patch vs {reference_label} size mismatch (max Δ "
            f"{_MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX}px per axis): "
            f"live {live_pw}×{live_ph} vs {reference_label} {ref_pw}×{ref_ph}."
        )


def _validate_template_vs_primary_bbox_patch_sizes(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    primary_bbox_percent: dict[str, float],
) -> None:
    """Ensure template H×W is plausible for the primary region on this frame.

    Raises ``ValueError`` when the labeled bbox resolves to a very different patch size than the
    crop PNG (e.g. stale asset vs current DPI). Small icons must match exactly pixel-for-pixel.
    """
    patch, _ = patch_bgr_from_bbox_percent(image_bgr, primary_bbox_percent)
    ph, pw = int(patch.shape[0]), int(patch.shape[1])
    th, tw = int(template_bgr.shape[0]), int(template_bgr.shape[1])
    validate_live_bbox_patch_vs_reference_dims(
        pw, ph, tw, th, reference_label="template PNG"
    )


def match_crop_1to1_at_bbox_percent(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    bbox_percent: dict[str, float],
) -> TemplateMatchResult:
    """Compare bbox patch to ``template_bgr`` pixel-wise (same shape only).

    No margin/pyramid: template must match what labeling exported from this bbox.
    """
    if template_bgr.ndim != 3:
        raise ValueError("Expected HxWx3 BGR template.")
    patch, (L, T) = patch_bgr_from_bbox_percent(image_bgr, bbox_percent)

    if patch.shape != template_bgr.shape:
        raise ValueError(
            f"1:1 shape mismatch: bbox patch {patch.shape} vs template {template_bgr.shape}. "
            "Use the same frame size as when exporting references/crop."
        )

    score, score_ncc, score_color, score_edge = _combined_match_score(patch, template_bgr)
    return TemplateMatchResult(
        score=score,
        top_left=(L, T),
        score_ncc=score_ncc,
        score_color=score_color,
        score_edge=score_edge,
        score_ncc_second=None,
    )


def match_template_in_search_roi_bbox_percent(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    search_bbox_percent: dict[str, float],
    *,
    exclude_top_lefts: list[tuple[int, int]] | None = None,
    exclude_radius_px: int = 0,
    primary_bbox_percent: dict[str, float] | None = None,
) -> TemplateMatchResult:
    """Slide ``template_bgr`` inside ROI from ``search_bbox_percent``.

    Returns best TM_CCOEFF_NORMED and global top-left on ``image_bgr``.

    When ``primary_bbox_percent`` is set (the labeled **detector** region), template dimensions must
    agree with that bbox cut out on ``image_bgr`` within :data:`_MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX`
    per axis; regions whose max side is under :data:`_SMALL_TEMPLATE_PRIMARY_MAX_SIDE_PX` require
    exact width/height equality (see module docstring).
    """
    if template_bgr.ndim != 3:
        raise ValueError("Expected HxWx3 BGR template.")
    if primary_bbox_percent is not None:
        _validate_template_vs_primary_bbox_patch_sizes(
            image_bgr, template_bgr, primary_bbox_percent
        )
    roi, (L, T) = patch_bgr_from_bbox_percent(image_bgr, search_bbox_percent)
    rh, rw = roi.shape[:2]
    th, tw = template_bgr.shape[:2]
    if th > rh or tw > rw or th < 1 or tw < 1:
        raise ValueError(
            f"Template {tw}×{th} must fit inside search ROI {rw}×{rh} "
            "(draw a larger **search_region** in Labeling)."
        )

    rg = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    heat = cv2.matchTemplate(rg, tg, cv2.TM_CCOEFF_NORMED)

    def _is_excluded(gx0: int, gy0: int) -> bool:
        if not exclude_top_lefts or exclude_radius_px <= 0:
            return False
        r2 = float(exclude_radius_px * exclude_radius_px)
        for ex, ey in exclude_top_lefts:
            dx = float(gx0 - int(ex))
            dy = float(gy0 - int(ey))
            if (dx * dx + dy * dy) <= r2:
                return True
        return False

    max_loc: tuple[int, int] | None = None
    max_val: float = -1.0
    # Try a few best NCC peaks, skipping excluded neighborhoods.
    for _ in range(25):
        _mn, cur_val, _mn_loc, cur_loc = cv2.minMaxLoc(heat)
        if cur_val <= -0.5:
            break
        x_off_i, y_off_i = int(cur_loc[0]), int(cur_loc[1])
        gx0 = int(L + x_off_i)
        gy0 = int(T + y_off_i)
        if not _is_excluded(gx0, gy0):
            max_loc = (x_off_i, y_off_i)
            max_val = float(cur_val)
            break
        # Mask this peak and retry.
        heat[y_off_i, x_off_i] = -1.0

    if max_loc is None:
        # Everything excluded or no valid peak; fall back to raw argmax.
        _mn, max_val, _mn_loc, max_loc0 = cv2.minMaxLoc(heat)
        max_loc = (int(max_loc0[0]), int(max_loc0[1]))

    x_off, y_off = max_loc
    gx = int(L + x_off)
    gy = int(T + y_off)
    patch = roi[y_off : y_off + th, x_off : x_off + tw]
    score_color = _color_similarity_score(patch, template_bgr)
    score_edge = _edge_similarity_score(patch, template_bgr)
    score_ncc = float(max_val)

    # Lowe-style peak uniqueness: the next-best NCC peak in a structurally
    # different location (masked ±template-size around the winner). Low-info /
    # smooth templates produce a plateau where many positions score nearly the
    # same — the overlay engine uses this to reject those false positives.
    score_ncc_second = _second_best_peak_ncc(heat, x_off, y_off, tw, th)
    return TemplateMatchResult(
        score=min(score_ncc, score_color, score_edge),
        top_left=(gx, gy),
        score_ncc=score_ncc,
        score_color=score_color,
        score_edge=score_edge,
        score_ncc_second=score_ncc_second,
    )


def _second_best_peak_ncc(
    heat: np.ndarray,
    best_x: int,
    best_y: int,
    template_w: int,
    template_h: int,
) -> float | None:
    """Best NCC value in ``heat`` after masking out a ±template-size box around the winner.

    Returns ``None`` when masking removes the entire heatmap (e.g. template barely smaller
    than ROI, no room for a structurally different second pick).
    """
    if heat.ndim != 2 or heat.size == 0:
        return None
    masked = heat.copy()
    hh, hw = masked.shape[:2]
    x0 = max(0, int(best_x) - int(template_w))
    y0 = max(0, int(best_y) - int(template_h))
    x1 = min(hw, int(best_x) + int(template_w))
    y1 = min(hh, int(best_y) + int(template_h))
    if x1 <= x0 or y1 <= y0:
        return None
    masked[y0:y1, x0:x1] = -1.0
    if not np.isfinite(masked).any() or float(np.max(masked)) <= -0.99:
        return None
    return float(np.max(masked))


def match_patch_bgr_at_top_left(
    image_bgr: np.ndarray,
    top_left: tuple[int, int],
    tw: int,
    th: int,
) -> np.ndarray | None:
    """Extract ``tw×th`` BGR patch at global ``top_left``; ``None`` if out of frame."""
    h, w = image_bgr.shape[:2]
    x0, y0 = int(top_left[0]), int(top_left[1])
    if x0 < 0 or y0 < 0 or tw < 1 or th < 1 or x0 + tw > w or y0 + th > h:
        return None
    return image_bgr[y0 : y0 + th, x0 : x0 + tw]


def patch_mean_hsv_saturation(patch_bgr: np.ndarray) -> float:
    """Mean HSV saturation (S channel, 0–255). Grey UI is usually low vs saturated blue buttons."""
    if patch_bgr.ndim != 3 or patch_bgr.size == 0:
        raise ValueError("Expected non-empty HxWx3 BGR patch.")
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 1]))
