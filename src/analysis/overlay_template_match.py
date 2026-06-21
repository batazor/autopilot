"""Template loading, matching, NCC, and match-quality gates for the overlay engine.

Extracted verbatim from ``analysis.overlay_engine``, which re-exports every
name here — keep importing via ``analysis.overlay_engine`` from consumers.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import TYPE_CHECKING

import cv2
import numpy as np

from layout.template_match import (
    TemplateMatchResult,
    match_patch_bgr_at_top_left,
    patch_bgr_from_bbox_percent,
    patch_mean_hsv_saturation,
)

if TYPE_CHECKING:
    from pathlib import Path

# Cap entry count; PNG crops are small (typically <10 KB each), so the upper
# bound on memory is well under 10 MB even when full. Sized to comfortably
# cover the active overlay rule fleet (~hundreds of distinct crops).
_TEMPLATE_CACHE_MAX = 512
_template_cache: OrderedDict[tuple[str, int], np.ndarray] = OrderedDict()
_template_mask_cache: OrderedDict[
    tuple[str, int], tuple[np.ndarray, np.ndarray | None]
] = OrderedDict()
_template_cache_lock = threading.Lock()


def _hybrid_sliding_matched(
    score: float,
    threshold: float,
    res: TemplateMatchResult,
) -> bool:
    """Sliding search: pHash, NCC *and* color at the peak must all clear ``threshold``.

    Uses ``min(pHash, NCC, color)`` so neither a high pHash alone (weak structural
    match) nor a strong grayscale match on the wrong color (e.g. a disabled grey
    button matching a blue template — pHash/NCC are colour-blind) can confirm a
    hit. ``score_color`` is per-pixel BGR similarity; on the pHash-only fast path
    it mirrors ``score``, so this is a no-op there and only adds teeth where a real
    colour score was computed.
    """
    candidates = [float(score)]
    score_ncc = res.get("score_ncc")
    if score_ncc is not None:
        candidates.append(float(score_ncc))
    score_color = res.get("score_color")
    if score_color is not None:
        candidates.append(float(score_color))
    return min(candidates) >= threshold


def _load_template_cached(path: Path) -> np.ndarray | None:
    """Decode a PNG template once, then reuse — invalidates on mtime change.

    Returned arrays are shared; callers must treat them as read-only (template
    match routines only sample, never mutate).
    """
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(path), mtime_ns)
    with _template_cache_lock:
        tpl = _template_cache.get(key)
        if tpl is not None:
            _template_cache.move_to_end(key)
            return tpl
    tpl = cv2.imread(str(path))
    if tpl is None:
        return None
    with _template_cache_lock:
        _template_cache[key] = tpl
        _template_cache.move_to_end(key)
        while len(_template_cache) > _TEMPLATE_CACHE_MAX:
            _template_cache.popitem(last=False)
    return tpl


def _load_template_with_mask_cached(path: Path) -> tuple[np.ndarray, np.ndarray | None] | None:
    """Decode a direct PNG template, preserving alpha as an optional match mask."""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(path), mtime_ns)
    with _template_cache_lock:
        cached = _template_mask_cache.get(key)
        if cached is not None:
            _template_mask_cache.move_to_end(key)
            return cached
    raw = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if raw is None:
        return None
    if raw.ndim == 3 and raw.shape[2] == 4:
        alpha = raw[:, :, 3]
        bgr = raw[:, :, :3]
        mask = (alpha > 8).astype(np.uint8) * 255
        ys, xs = np.where(mask > 0)
        if len(xs) and len(ys):
            bgr = bgr[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]
            mask = mask[int(ys.min()) : int(ys.max()) + 1, int(xs.min()) : int(xs.max()) + 1]
        if mask.size and bool(np.all(mask == 255)):
            mask = None
        out = (bgr, mask)
    else:
        out = (raw, None)
    with _template_cache_lock:
        _template_mask_cache[key] = out
        _template_mask_cache.move_to_end(key)
        while len(_template_mask_cache) > _TEMPLATE_CACHE_MAX:
            _template_mask_cache.popitem(last=False)
    return out


def _masked_zero_mean_ncc(
    template_bgr: np.ndarray,
    patch_bgr: np.ndarray,
    mask: np.ndarray | None,
) -> float:
    """Mean-centered normalized cross-correlation over the masked template pixels.

    ``TM_CCORR_NORMED`` (used to *locate* the peak) is not mean-centered, so a
    uniformly bright window scores near 1.0 against almost any template. This
    subtracts each image's mean before correlating, so only structurally similar
    content scores high. Returns ``[0, 1]`` (negative correlation clamps to 0).

    A flat template (no structure to verify) returns 1.0 — there is nothing to
    confirm, so we defer to the locating score and avoid regressing thresholds
    tuned for solid-color icons. A flat candidate under a structured template
    returns 0.0 (cannot be a real match).
    """
    if template_bgr.shape[:2] != patch_bgr.shape[:2]:
        return 0.0
    t = cv2.cvtColor(template_bgr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    p = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2GRAY).astype(np.float64)
    mask_2d = None
    if mask is not None:
        mask_2d = mask if mask.ndim == 2 else mask[:, :, 0]
    m = np.ones(t.shape, dtype=bool) if mask_2d is None else (mask_2d > 0)
    if int(np.count_nonzero(m)) < 2:
        return 1.0
    tv = t[m]
    pv = p[m]
    tv = tv - tv.mean()
    pv = pv - pv.mean()
    t_norm = float(np.sqrt(np.dot(tv, tv)))
    p_norm = float(np.sqrt(np.dot(pv, pv)))
    if t_norm < 1e-6:
        return 1.0
    if p_norm < 1e-6:
        return 0.0
    return max(0.0, float(np.dot(tv, pv) / (t_norm * p_norm)))


def _match_direct_template_in_bbox(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    template_mask: np.ndarray | None,
    search_bbox: dict[str, float],
) -> TemplateMatchResult:
    search, (left, top) = patch_bgr_from_bbox_percent(image_bgr, search_bbox)
    tw = int(template_bgr.shape[1])
    th = int(template_bgr.shape[0])
    if tw <= search.shape[1] and th <= search.shape[0]:
        heat = cv2.matchTemplate(search, template_bgr, cv2.TM_CCORR_NORMED, mask=template_mask)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(heat)
        if not np.isfinite(max_val):
            max_val = 0.0
        x0, y0 = int(max_loc[0]), int(max_loc[1])
        patch = search[y0 : y0 + th, x0 : x0 + tw]
        zero_mean_ncc = _masked_zero_mean_ncc(template_bgr, patch, template_mask)
        return TemplateMatchResult(
            score=float(max_val),
            top_left=(int(left + x0), int(top + y0)),
            score_ncc=zero_mean_ncc,
            score_ncc_second=None,
            match_source="direct_template",
            hash_distance=None,
            template_w=tw,
            template_h=th,
        )
    return TemplateMatchResult(
        score=0.0,
        top_left=(int(left), int(top)),
        score_ncc=0.0,
        score_ncc_second=None,
        match_source="direct_template",
        hash_distance=None,
        template_w=tw,
        template_h=th,
    )


def _apply_min_saturation_gate(
    image_bgr: np.ndarray,
    top_left: tuple[int, int],
    tw: int,
    th: int,
    min_s: float,
) -> tuple[bool, float | None, str | None]:
    """Returns ``(passes, mean_saturation_or_none, fail_reason_or_none)``."""
    patch = match_patch_bgr_at_top_left(image_bgr, top_left, tw, th)
    if patch is None:
        return False, None, "match_patch_out_of_bounds"
    mean_s = patch_mean_hsv_saturation(patch)
    if mean_s < float(min_s):
        return False, mean_s, "low_saturation"
    return True, mean_s, None


def _bright_low_saturation_ratio(patch_bgr: np.ndarray) -> float:
    """Share of bright low-saturation pixels (white/cream UI details)."""
    if patch_bgr.ndim != 3 or patch_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 1] <= 45) & (hsv[:, :, 2] >= 150)
    return float(np.mean(mask))


def _apply_bright_detail_gate(
    image_bgr: np.ndarray,
    template_bgr: np.ndarray,
    top_left: tuple[int, int],
) -> tuple[bool, float, float, str | None]:
    """Reject matches that lose distinctive bright low-saturation template details.

    This is intentionally automatic rather than YAML-driven: when a template contains a large
    white/cream component (for example a sleeve, text, or border), a candidate patch with almost
    none of that component is usually a geometric false positive.
    """
    template_ratio = _bright_low_saturation_ratio(template_bgr)
    # Below this share of bright low-S pixels in the reference crop, skip the gate —
    # only clearly white-heavy templates (icons with lots of cream/UI chrome) need it.
    _BRIGHT_DETAIL_TEMPLATE_MIN = 0.35
    if template_ratio < _BRIGHT_DETAIL_TEMPLATE_MIN:
        return True, template_ratio, 0.0, None
    patch = match_patch_bgr_at_top_left(
        image_bgr,
        top_left,
        int(template_bgr.shape[1]),
        int(template_bgr.shape[0]),
    )
    if patch is None:
        return False, template_ratio, 0.0, "match_patch_out_of_bounds"
    patch_ratio = _bright_low_saturation_ratio(patch)
    min_ratio = max(0.12, template_ratio * 0.35)
    if patch_ratio < min_ratio:
        return False, template_ratio, patch_ratio, "low_bright_detail_ratio"
    return True, template_ratio, patch_ratio, None

