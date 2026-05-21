"""Compare an exported crop with the **same bbox rectangle** on another frame (1:1, no search).

Uses the same pixel rounding as :func:`ui.area_annotator.crop_region` / crop export.
The live frame must be the **same resolution** as when the crop was produced.

**1:1** (:func:`match_crop_1to1_at_bbox_percent`) scores only **pHash** (tolerates animated UI).

**Sliding search** (ROI or full frame) uses **NCC** (``matchTemplate`` heatmap) to propose peaks,
then scores each candidate with **pHash** and picks the best pHash. ``score_ncc`` / color / edge
are reported for debugging; ``score_ncc_second`` comes from the NCC heatmap for peak-uniqueness gates.
"""
from __future__ import annotations

import hashlib
import math
import threading
from collections import OrderedDict
from typing import TypedDict

import cv2
import numpy as np

from layout.search_cache import read_positions, record_position

# Live bbox patch vs exported template (sliding search): reject gross size mismatch.
_MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX = 10
# If either side is under this (max of W/H), require exact pixel dimensions (strict 1:1).
_SMALL_TEMPLATE_PRIMARY_MAX_SIDE_PX = 20
_PHASH_BITS = 64
_NCC_PEAK_MAX_ROI = 25
_NCC_PEAK_MAX_FULL = 40
_NCC_PEAK_MIN_VAL = -0.5
# Flat patches yield spurious TM_CCOEFF_NORMED ≈ 1.0; skip them when picking peaks.
_MIN_PATCH_GRAY_STD = 5.0
_TEMPLATE_PHASH_CACHE_MAX = 512
_template_phash_cache: OrderedDict[bytes, int] = OrderedDict()
_template_gray_cache: OrderedDict[bytes, np.ndarray] = OrderedDict()
_template_cache_lock = threading.Lock()


class TemplateMatchResult(TypedDict, total=False):
    # pHash similarity ``1 - hamming/64``.
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
    match_source: str
    hash_distance: int | None
    template_w: int
    template_h: int


def _color_similarity_score(patch_bgr: np.ndarray, template_bgr: np.ndarray) -> float:
    """Return a strict per-pixel BGR similarity score in ``[0, 1]``.

    Grayscale normalized correlation can be very high for the wrong UI patch when gradients line up.
    Capping it with color similarity rejects matches that miss saturated landmarks (red crosshair,
    yellow border, blue button, etc.).
    """
    if patch_bgr.shape != template_bgr.shape:
        msg = f"Color score shape mismatch: patch {patch_bgr.shape} vs template {template_bgr.shape}."
        raise ValueError(
            msg
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
        msg = f"Edge score shape mismatch: patch {patch_bgr.shape} vs template {template_bgr.shape}."
        raise ValueError(
            msg
        )
    pg = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    pe = cv2.Canny(pg, 50, 150)
    te = cv2.Canny(tg, 50, 150)
    diff = np.abs(pe.astype(np.float32) - te.astype(np.float32))
    mae = float(np.mean(diff))
    return max(0.0, min(1.0, 1.0 - mae / 255.0))


def _phash64(patch_bgr: np.ndarray) -> int:
    gray = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
    # Light blur before DCT reduces anti-alias / glow drift on text strips (e.g.
    # welcome_back) without moving self-match scores on crisp icon crops (shop).
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(small))
    block = dct[:8, :8].copy()
    block[0, 0] = 0
    med = float(np.median(block))
    bits = (block >= med).astype(np.uint8).reshape(-1)
    out = 0
    for bit in bits:
        out = (out << 1) | int(bit)
    return out


def _template_cache_key_bytes(template_bgr: np.ndarray) -> bytes:
    return hashlib.blake2b(np.ascontiguousarray(template_bgr).tobytes(), digest_size=16).digest()


def _phash64_template_cached(template_bgr: np.ndarray) -> int:
    key = _template_cache_key_bytes(template_bgr)
    with _template_cache_lock:
        hit = _template_phash_cache.get(key)
        if hit is not None:
            _template_phash_cache.move_to_end(key)
            return hit
    digest = _phash64(template_bgr)
    with _template_cache_lock:
        _template_phash_cache[key] = digest
        _template_phash_cache.move_to_end(key)
        while len(_template_phash_cache) > _TEMPLATE_PHASH_CACHE_MAX:
            _template_phash_cache.popitem(last=False)
    return digest


def _template_gray_cached(template_bgr: np.ndarray) -> np.ndarray:
    key = _template_cache_key_bytes(template_bgr)
    with _template_cache_lock:
        hit = _template_gray_cache.get(key)
        if hit is not None:
            _template_gray_cache.move_to_end(key)
            return hit
    gray = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
    with _template_cache_lock:
        _template_gray_cache[key] = gray
        _template_gray_cache.move_to_end(key)
        while len(_template_gray_cache) > _TEMPLATE_PHASH_CACHE_MAX:
            _template_gray_cache.popitem(last=False)
    return gray


def _phash_hamming_distance(patch_bgr: np.ndarray, template_bgr: np.ndarray) -> int:
    return _hamming64(_phash64(patch_bgr), _phash64_template_cached(template_bgr))


def _phash_similarity_score(hamming: int) -> float:
    return max(0.0, min(1.0, 1.0 - hamming / float(_PHASH_BITS)))


def _phash_match_score(
    patch_bgr: np.ndarray,
    template_bgr: np.ndarray,
) -> tuple[float, int]:
    hamming = _phash_hamming_distance(patch_bgr, template_bgr)
    return _phash_similarity_score(hamming), hamming


def _template_match_result_from_phash(
    *,
    score: float,
    hamming: int,
    top_left: tuple[int, int],
    match_source: str | None = None,
    score_second: float | None = None,
) -> TemplateMatchResult:
    """Build a 1:1 result dict; legacy ``score_ncc`` fields mirror pHash for UI consumers."""
    out: TemplateMatchResult = {
        "score": score,
        "top_left": top_left,
        "score_ncc": score,
        "score_color": score,
        "score_edge": score,
        "score_ncc_second": score_second,
        "hash_distance": hamming,
    }
    if match_source is not None:
        out["match_source"] = match_source
    return out


def _peak_rank_score(ncc: float, color: float, edge: float) -> float:
    """Rank NCC peaks for localization (structural + color + edges)."""
    return min(float(ncc), float(color), float(edge))


def _hybrid_result_at_patch(
    patch_bgr: np.ndarray,
    template_bgr: np.ndarray,
    top_left: tuple[int, int],
    *,
    ncc_at_peak: float | None = None,
    match_source: str | None = None,
) -> TemplateMatchResult:
    phash_s, hamming_s, ncc_s, color_s, edge_s = _hybrid_scores_at_patch(
        patch_bgr, template_bgr, ncc_at_peak=ncc_at_peak
    )
    return _template_match_result_hybrid(
        score=phash_s,
        hamming=hamming_s,
        top_left=top_left,
        score_ncc=ncc_s,
        score_color=color_s,
        score_edge=edge_s,
        match_source=match_source,
    )


def _hybrid_scores_at_patch(
    patch_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    ncc_at_peak: float | None = None,
) -> tuple[float, int, float, float, float]:
    """Return ``(phash_score, hamming, ncc, color, edge)`` for a candidate patch."""
    phash_score, hamming = _phash_match_score(patch_bgr, template_bgr)
    color = _color_similarity_score(patch_bgr, template_bgr)
    edge = _edge_similarity_score(patch_bgr, template_bgr)
    if ncc_at_peak is not None:
        ncc = float(ncc_at_peak)
    else:
        pg = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY)
        tg = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY)
        ncc = float(cv2.matchTemplate(pg, tg, cv2.TM_CCOEFF_NORMED)[0, 0])
    return phash_score, hamming, ncc, color, edge


def _template_match_result_hybrid(
    *,
    score: float,
    hamming: int,
    top_left: tuple[int, int],
    score_ncc: float,
    score_color: float,
    score_edge: float,
    score_ncc_second: float | None = None,
    match_source: str | None = None,
) -> TemplateMatchResult:
    out: TemplateMatchResult = {
        "score": score,
        "top_left": top_left,
        "score_ncc": score_ncc,
        "score_color": score_color,
        "score_edge": score_edge,
        "score_ncc_second": score_ncc_second,
        "hash_distance": hamming,
    }
    if match_source is not None:
        out["match_source"] = match_source
    return out


def _patch_gray_std(
    region_gray: np.ndarray | None,
    region_bgr: np.ndarray,
    y_off: int,
    x_off: int,
    th: int,
    tw: int,
) -> float:
    if region_gray is not None:
        patch_gray = region_gray[y_off : y_off + th, x_off : x_off + tw]
    else:
        patch_gray = cv2.cvtColor(
            region_bgr[y_off : y_off + th, x_off : x_off + tw], cv2.COLOR_BGR2GRAY
        )
    return float(np.std(patch_gray))


def _best_phash_among_ncc_peaks(
    region_bgr: np.ndarray,
    template_bgr: np.ndarray,
    origin_xy: tuple[int, int],
    heat_orig: np.ndarray,
    *,
    max_peaks: int,
    exclude_top_lefts: list[tuple[int, int]] | None = None,
    exclude_radius_px: int = 0,
    threshold: float | None = None,
    region_gray: np.ndarray | None = None,
) -> TemplateMatchResult | None:
    """Pick the NCC peak with the best ``min(ncc, color, edge)`` inside ``region_bgr``.

    The peak loop scores with pHash + heatmap NCC; color/edge run only for peaks
    that can beat the current rank or when ``threshold`` is met (overlay match
    uses pHash ``score``).
    """
    th, tw = int(template_bgr.shape[0]), int(template_bgr.shape[1])
    ox, oy = int(origin_xy[0]), int(origin_xy[1])
    heat_scan = heat_orig.copy()
    hm, wm = int(heat_scan.shape[0]), int(heat_scan.shape[1])

    template_has_texture = float(np.std(_template_gray_cached(template_bgr))) >= _MIN_PATCH_GRAY_STD

    best: TemplateMatchResult | None = None
    best_rank = -1.0
    best_x_off = 0
    best_y_off = 0
    found = False

    def _maybe_update_best(
        patch_i: np.ndarray,
        x_off_i: int,
        y_off_i: int,
        ncc_i: float,
    ) -> TemplateMatchResult | None:
        nonlocal best, best_rank, best_x_off, best_y_off, found
        phash_i, hamming_i = _phash_match_score(patch_i, template_bgr)
        found = True
        if threshold is not None and phash_i >= threshold:
            out = _hybrid_result_at_patch(
                patch_i,
                template_bgr,
                (ox + x_off_i, oy + y_off_i),
                ncc_at_peak=ncc_i,
            )
            out["score_ncc_second"] = _second_best_peak_ncc(
                heat_orig, x_off_i, y_off_i, tw, th
            )
            return out
        if min(ncc_i, phash_i) <= best_rank:
            return None
        color_i = _color_similarity_score(patch_i, template_bgr)
        edge_i = _edge_similarity_score(patch_i, template_bgr)
        pick_i = _peak_rank_score(ncc_i, color_i, edge_i)
        if pick_i <= best_rank:
            return None
        best_rank = pick_i
        best_x_off = x_off_i
        best_y_off = y_off_i
        best = _template_match_result_hybrid(
            score=phash_i,
            hamming=hamming_i,
            top_left=(ox + x_off_i, oy + y_off_i),
            score_ncc=ncc_i,
            score_color=color_i,
            score_edge=edge_i,
        )
        return None

    for _ in range(max_peaks):
        _mn, cur_val, _mn_loc, cur_loc = cv2.minMaxLoc(heat_scan)
        if float(cur_val) <= _NCC_PEAK_MIN_VAL:
            break
        x_off_i, y_off_i = int(cur_loc[0]), int(cur_loc[1])
        gx0 = ox + x_off_i
        gy0 = oy + y_off_i
        if _is_top_left_excluded(
            gx0, gy0, exclude_top_lefts=exclude_top_lefts, exclude_radius_px=exclude_radius_px
        ):
            heat_scan[y_off_i, x_off_i] = -1.0
            continue
        if template_has_texture and _patch_gray_std(
            region_gray, region_bgr, y_off_i, x_off_i, th, tw
        ) < _MIN_PATCH_GRAY_STD:
            heat_scan[y_off_i, x_off_i] = -1.0
            continue
        patch_i = region_bgr[y_off_i : y_off_i + th, x_off_i : x_off_i + tw]
        early = _maybe_update_best(patch_i, x_off_i, y_off_i, float(cur_val))
        if early is not None:
            return early
        y1 = min(hm, y_off_i + th)
        x1 = min(wm, x_off_i + tw)
        heat_scan[y_off_i:y1, x_off_i:x1] = -1.0

    if best is not None:
        best["score_ncc_second"] = _second_best_peak_ncc(
            heat_orig, best_x_off, best_y_off, tw, th
        )
        return best

    return None


def template_cache_key(
    *,
    region_name: str,
    reference_rel: str,
    template_bgr: np.ndarray,
    screen_shape: tuple[int, int],
) -> str:
    digest = hashlib.sha256(np.ascontiguousarray(template_bgr).tobytes()).hexdigest()
    h, w = int(screen_shape[0]), int(screen_shape[1])
    th, tw = int(template_bgr.shape[0]), int(template_bgr.shape[1])
    return f"{region_name}|{reference_rel}|{digest}|{w}x{h}|{tw}x{th}"


def _hamming64(a: int, b: int) -> int:
    return int((int(a) ^ int(b)).bit_count())


def _bbox_px_bounds(
    bbox_percent: dict[str, float],
    *,
    hi: int,
    wi: int,
) -> tuple[int, int, int, int]:
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
    return L, T, R, B


def bgr_view_from_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
) -> tuple[np.ndarray, tuple[int, int]]:
    """BBox slice as a view (no copy); same rounding as :func:`patch_bgr_from_bbox_percent`."""
    if image_bgr.ndim != 3:
        msg = "Expected HxWx3 BGR image."
        raise ValueError(msg)
    hi, wi = image_bgr.shape[:2]
    L, T, R, B = _bbox_px_bounds(bbox_percent, hi=hi, wi=wi)
    return image_bgr[T:B, L:R], (L, T)


def gray_view_from_bbox_percent(
    image_gray: np.ndarray,
    bbox_percent: dict[str, float],
) -> tuple[np.ndarray, tuple[int, int]]:
    """Grayscale bbox slice as a view; same rounding as :func:`patch_bgr_from_bbox_percent`."""
    if image_gray.ndim != 2:
        msg = "Expected HxW grayscale image."
        raise ValueError(msg)
    hi, wi = image_gray.shape[:2]
    L, T, R, B = _bbox_px_bounds(bbox_percent, hi=hi, wi=wi)
    return image_gray[T:B, L:R], (L, T)


def patch_bgr_from_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
) -> tuple[np.ndarray, tuple[int, int]]:
    """Cut out the bbox rectangle in pixels (percent of frame); mirrors labeling crop rounding."""
    if image_bgr.ndim != 3:
        msg = "Expected HxWx3 BGR image."
        raise ValueError(msg)
    view, origin = bgr_view_from_bbox_percent(image_bgr, bbox_percent)
    return view.copy(), origin


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
            msg = (
                f"Small-region: live bbox patch {live_pw}×{live_ph} must match {reference_label} "
                f"{ref_pw}×{ref_ph} exactly (1:1)."
            )
            raise ValueError(
                msg
            )
        return
    if (
        abs(live_pw - ref_pw) > _MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX
        or abs(live_ph - ref_ph) > _MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX
    ):
        msg = (
            f"Live bbox patch vs {reference_label} size mismatch (max Δ "
            f"{_MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX}px per axis): "
            f"live {live_pw}×{live_ph} vs {reference_label} {ref_pw}×{ref_ph}."
        )
        raise ValueError(
            msg
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


def _is_top_left_excluded(
    x0: int,
    y0: int,
    *,
    exclude_top_lefts: list[tuple[int, int]] | None,
    exclude_radius_px: int,
) -> bool:
    if not exclude_top_lefts or exclude_radius_px <= 0:
        return False
    r2 = float(exclude_radius_px * exclude_radius_px)
    for ex, ey in exclude_top_lefts:
        dx = float(x0 - int(ex))
        dy = float(y0 - int(ey))
        if (dx * dx + dy * dy) <= r2:
            return True
    return False


def match_crop_1to1_at_bbox_percent(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    bbox_percent: dict[str, float],
) -> TemplateMatchResult:
    """Compare bbox patch to ``template_bgr`` via pHash (same shape only).

    No margin/pyramid: template must match what labeling exported from this bbox.
    """
    if template_bgr.ndim != 3:
        msg = "Expected HxWx3 BGR template."
        raise ValueError(msg)
    patch, (L, T) = patch_bgr_from_bbox_percent(image_bgr, bbox_percent)

    if patch.shape != template_bgr.shape:
        msg = (
            f"1:1 shape mismatch: bbox patch {patch.shape} vs template {template_bgr.shape}. "
            "Use the same frame size as when exporting references/crop."
        )
        raise ValueError(
            msg
        )

    score, hamming = _phash_match_score(patch, template_bgr)
    return _template_match_result_from_phash(score=score, hamming=hamming, top_left=(L, T))


def match_template_in_search_roi_bbox_percent(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    search_bbox_percent: dict[str, float],
    *,
    exclude_top_lefts: list[tuple[int, int]] | None = None,
    exclude_radius_px: int = 0,
    primary_bbox_percent: dict[str, float] | None = None,
    image_gray: np.ndarray | None = None,
    threshold: float | None = None,
) -> TemplateMatchResult:
    """Slide ``template_bgr`` inside ROI: NCC peaks, best pHash among candidates.

    When ``primary_bbox_percent`` is set (the labeled **detector** region), template dimensions must
    agree with that bbox cut out on ``image_bgr`` within :data:`_MAX_TEMPLATE_PRIMARY_PATCH_DELTA_PX`
    per axis; regions whose max side is under :data:`_SMALL_TEMPLATE_PRIMARY_MAX_SIDE_PX` require
    exact width/height equality (see module docstring).
    """
    if template_bgr.ndim != 3:
        msg = "Expected HxWx3 BGR template."
        raise ValueError(msg)
    if primary_bbox_percent is not None:
        _validate_template_vs_primary_bbox_patch_sizes(
            image_bgr, template_bgr, primary_bbox_percent
        )
    roi, (L, T) = bgr_view_from_bbox_percent(image_bgr, search_bbox_percent)
    rh, rw = roi.shape[:2]
    th, tw = template_bgr.shape[:2]
    if th > rh or tw > rw or th < 1 or tw < 1:
        msg = (
            f"Template {tw}×{th} must fit inside search ROI {rw}×{rh} "
            "(draw a larger **search_region** in Labeling)."
        )
        raise ValueError(msg)

    if image_gray is not None:
        roi_gray, _ = gray_view_from_bbox_percent(image_gray, search_bbox_percent)
    else:
        roi_gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    tg = _template_gray_cached(template_bgr)
    heat_orig = cv2.matchTemplate(roi_gray, tg, cv2.TM_CCOEFF_NORMED)
    best = _best_phash_among_ncc_peaks(
        roi,
        template_bgr,
        (L, T),
        heat_orig,
        max_peaks=_NCC_PEAK_MAX_ROI,
        exclude_top_lefts=exclude_top_lefts,
        exclude_radius_px=exclude_radius_px,
        threshold=threshold,
        region_gray=roi_gray,
    )
    if best is not None:
        return best

    if exclude_top_lefts:
        return _template_match_result_hybrid(
            score=0.0,
            hamming=_PHASH_BITS,
            top_left=(L, T),
            score_ncc=0.0,
            score_color=0.0,
            score_edge=0.0,
            score_ncc_second=None,
        )

    _mn, max_val, _mn_loc, max_loc = cv2.minMaxLoc(heat_orig)
    x_off, y_off = int(max_loc[0]), int(max_loc[1])
    gx, gy = L + x_off, T + y_off
    if _is_top_left_excluded(
        gx, gy, exclude_top_lefts=exclude_top_lefts, exclude_radius_px=exclude_radius_px
    ):
        return _template_match_result_hybrid(
            score=0.0,
            hamming=_PHASH_BITS,
            top_left=(gx, gy),
            score_ncc=float(max_val),
            score_color=0.0,
            score_edge=0.0,
            score_ncc_second=None,
        )
    patch = roi[y_off : y_off + th, x_off : x_off + tw]
    if float(np.std(_template_gray_cached(template_bgr))) >= _MIN_PATCH_GRAY_STD and _patch_gray_std(
        roi_gray, roi, y_off, x_off, th, tw
    ) < _MIN_PATCH_GRAY_STD:
        return _template_match_result_hybrid(
            score=0.0,
            hamming=_PHASH_BITS,
            top_left=(gx, gy),
            score_ncc=float(max_val),
            score_color=0.0,
            score_edge=0.0,
            score_ncc_second=None,
        )
    out = _hybrid_result_at_patch(
        patch, template_bgr, (gx, gy), ncc_at_peak=float(max_val)
    )
    out["score_ncc_second"] = _second_best_peak_ncc(heat_orig, x_off, y_off, tw, th)
    return out


def _full_frame_fallback_peak(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    heat_orig: np.ndarray,
    th: int,
    tw: int,
    *,
    exclude_top_lefts: list[tuple[int, int]] | None,
    exclude_radius_px: int,
) -> TemplateMatchResult:
    """Pick a peak when :func:`_best_phash_among_ncc_peaks` found nothing.

    When exclusions are active, scan the NCC heatmap and skip suppressed
    top-lefts so ``while_match`` loops do not rediscover the same button.
    Without exclusions, keep the legacy single ``minMaxLoc`` pick.
    """
    if exclude_top_lefts and exclude_radius_px > 0:
        heat_work = heat_orig.copy()
        for _ in range(_NCC_PEAK_MAX_FULL):
            _mn, max_val, _mn_loc, max_loc = cv2.minMaxLoc(heat_work)
            if float(max_val) <= _NCC_PEAK_MIN_VAL:
                break
            x0, y0 = int(max_loc[0]), int(max_loc[1])
            if _is_top_left_excluded(
                x0,
                y0,
                exclude_top_lefts=exclude_top_lefts,
                exclude_radius_px=exclude_radius_px,
            ):
                heat_work[y0, x0] = -1.0
                continue
            patch = image_bgr[y0 : y0 + th, x0 : x0 + tw]
            out = _hybrid_result_at_patch(
                patch,
                template_bgr,
                (x0, y0),
                ncc_at_peak=float(max_val),
                match_source="full_frame_ncc_phash",
            )
            out["score_ncc_second"] = _second_best_peak_ncc(heat_orig, x0, y0, tw, th)
            return out
        return _template_match_result_hybrid(
            score=0.0,
            hamming=_PHASH_BITS,
            top_left=(0, 0),
            score_ncc=0.0,
            score_color=0.0,
            score_edge=0.0,
            score_ncc_second=None,
            match_source="full_frame_ncc_phash",
        )

    _mn, max_val, _mn_loc, max_loc = cv2.minMaxLoc(heat_orig)
    x0, y0 = int(max_loc[0]), int(max_loc[1])
    patch = image_bgr[y0 : y0 + th, x0 : x0 + tw]
    out = _hybrid_result_at_patch(
        patch,
        template_bgr,
        (x0, y0),
        ncc_at_peak=float(max_val),
        match_source="full_frame_ncc_phash",
    )
    out["score_ncc_second"] = _second_best_peak_ncc(heat_orig, x0, y0, tw, th)
    return out


def match_template_full_frame_cached(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    *,
    cache_key: str,
    threshold: float,
    exclude_top_lefts: list[tuple[int, int]] | None = None,
    exclude_radius_px: int = 0,
    image_gray: np.ndarray | None = None,
) -> TemplateMatchResult:
    """Search the whole frame: cached positions first, then NCC peaks scored with pHash."""
    if template_bgr.ndim != 3:
        msg = "Expected HxWx3 BGR template."
        raise ValueError(msg)
    h, w = image_bgr.shape[:2]
    th, tw = template_bgr.shape[:2]
    if th > h or tw > w or th < 1 or tw < 1:
        msg = f"Template {tw}×{th} must fit inside frame {w}×{h}."
        raise ValueError(msg)

    def _score_at(x0: int, y0: int, source: str) -> TemplateMatchResult | None:
        if x0 < 0 or y0 < 0 or x0 + tw > w or y0 + th > h:
            return None
        if _is_top_left_excluded(
            x0, y0, exclude_top_lefts=exclude_top_lefts, exclude_radius_px=exclude_radius_px
        ):
            return None
        patch = image_bgr[y0 : y0 + th, x0 : x0 + tw]
        phash_s, hamming_s = _phash_match_score(patch, template_bgr)
        if phash_s >= threshold:
            return _hybrid_result_at_patch(
                patch,
                template_bgr,
                (int(x0), int(y0)),
                match_source=source,
            )
        return _template_match_result_from_phash(
            score=phash_s,
            hamming=hamming_s,
            top_left=(int(x0), int(y0)),
            match_source=source,
        )

    best: TemplateMatchResult | None = None
    for row in read_positions(cache_key):
        cand = _score_at(int(row["x"]), int(row["y"]), "cache")
        if cand is None:
            continue
        if best is None or float(cand["score"]) > float(best["score"]):
            best = cand
        if float(cand["score"]) >= threshold:
            record_position(
                cache_key,
                x=int(cand["top_left"][0]),
                y=int(cand["top_left"][1]),
                score=float(cand["score"]),
            )
            return cand

    rg = image_gray if image_gray is not None else cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    tg = _template_gray_cached(template_bgr)
    heat_orig = cv2.matchTemplate(rg, tg, cv2.TM_CCOEFF_NORMED)
    scanned = _best_phash_among_ncc_peaks(
        image_bgr,
        template_bgr,
        (0, 0),
        heat_orig,
        max_peaks=_NCC_PEAK_MAX_FULL,
        exclude_top_lefts=exclude_top_lefts,
        exclude_radius_px=exclude_radius_px,
        threshold=threshold,
        region_gray=rg,
    )
    if scanned is not None:
        scanned["match_source"] = "full_frame_ncc_phash"
    else:
        scanned = _full_frame_fallback_peak(
            image_bgr,
            template_bgr,
            heat_orig,
            th,
            tw,
            exclude_top_lefts=exclude_top_lefts,
            exclude_radius_px=exclude_radius_px,
        )

    if best is None or float(scanned["score"]) > float(best["score"]):
        best = scanned
    if best is not None and float(best["score"]) >= threshold:
        record_position(
            cache_key,
            x=int(best["top_left"][0]),
            y=int(best["top_left"][1]),
            score=float(best["score"]),
        )
    return best if best is not None else scanned


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
        msg = "Expected non-empty HxWx3 BGR patch."
        raise ValueError(msg)
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    return float(np.mean(hsv[:, :, 1]))
