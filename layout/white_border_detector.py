"""Programmatic "white border around icon" detector.

Some UIs highlight a claimable / selected icon by drawing a thin, near-white
1-frame outline around it (e.g. the VIP Point Rewards screen marks the
claimable reward tile with a bright outline while inactive tiles sit on the
plain saturated-cyan card background).

The detector samples a thin halo *just outside* the labeled bbox of the icon
and asks three questions of those pixels:

* are they bright (HSV V is high)?
* are they de-saturated (HSV S is low)?
* is the icon **interior** more saturated than the halo? — i.e. is there
  actually a contrasting colored thing inside the bbox, not just empty
  light-grey UI?

All three must pass. The first two alone fire on any random bbox that lands
on a light UI panel (mail screens, worker-add overlays — both have large
near-white backgrounds where any halo would look like a frame). The third
gate isolates the case where a *colored icon* is wrapped by a near-white
outline against a more-saturated surround.

Calibration on ``tests/fixtures/vip_point_rewards_white_border.png``
(VIP Point Rewards, 720×1280; 4 reward tiles, first one is claimable):

* claimable tile:  halo ``S≈95 V≈245``,  interior ``S≈116`` → gap +21;
* inactive tiles:  halo ``S≈188 V≈225``, interior ``S≈115`` → gap ≈ −75.

The defaults below sit safely inside the gaps:

* ``max_mean_saturation = 140`` — halo S floor for "is the halo desaturated".
* ``min_mean_value = 200`` — halo V floor for "is the halo bright".
* ``min_interior_saturation_excess = 15`` — interior S must beat halo S by
  this much; rejects white-on-white panel regions where both are desaturated.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

WHITE_BORDER_HALO_PX = 3
"""Width of the halo ring (pixels) sampled just outside the bbox. Small enough
to stay on the highlight outline itself, large enough to average out per-pixel
JPEG-style noise from the captured frame."""

WHITE_BORDER_MAX_MEAN_SATURATION = 140.0
"""Mean HSV saturation of the halo must be **below** this to call the icon
highlighted. A real white outline desaturates the halo to S≈112; a halo that
samples the bare saturated row background sits at S≈188-192."""

WHITE_BORDER_MIN_MEAN_VALUE = 200.0
"""Mean HSV value of the halo must be **above** this. Guards against dark
backgrounds where low S would otherwise be ambiguous (grey shadow vs white
highlight). On the calibration screen all tiles have V≈228-243 so this gate
is permissive by design — discrimination is carried by saturation."""

WHITE_BORDER_MIN_RING_PIXELS = 24
"""Minimum halo pixel count needed to trust the means. When the bbox is at
the image edge the halo gets clipped on one or more sides; below this floor
we abstain rather than decide on a tiny sample."""

WHITE_BORDER_INTERIOR_SAMPLE_PCT = 0.6
"""Fraction of the bbox (centered) sampled as the icon interior. Trimming the
outer 20% on each axis avoids picking up the halo itself from inside (the
white outline often bleeds 1-2 px into the labeled bbox)."""

WHITE_BORDER_MIN_INTERIOR_SATURATION_EXCESS = 10.0
"""Interior mean S must exceed halo mean S by at least this much. Without
this gate, the detector fires on any bbox sitting in a uniformly light UI
region (mail / worker-add screens are full of such patches). The claimable
tile in the calibration animation cycles its halo between S≈91 and S≈100
(gap +25 down to +16) — a floor of 10 leaves >6 points of margin at the
animation's dimmest sampled frame while still cleanly rejecting indistinct
white-on-white regions (those sit at gaps ≤ +5 in practice)."""


# ---------------------------------------------------------------------------
# Contour-based "find the highlight" search
# ---------------------------------------------------------------------------
#
# :func:`has_white_border_in_bbox_percent` works when the caller already knows
# where the icon is and just needs a yes/no classifier. For sliding-template
# regions like ``button.claim`` (the claim popup may render the button in
# different places per popup) the labeled bbox is *not* where the highlight
# actually appears — we have to **find** the white outline first.
#
# Brute-force sliding window over the search ROI produces thousands of false
# positives because any near-white halo plus any saturated interior passes the
# gates (yellow VIP cards with dark lock icons mimic the pattern almost
# perfectly). Switching to contour detection cuts the candidate pool to a
# handful: a real claim-button outline is a thin, closed near-white rectangle
# wrapping a saturated icon body, while text labels, lock badges and gradient
# edges fail at least one of the geometry filters.

WHITE_FRAME_NEAR_WHITE_MIN_VALUE = 200
"""HSV V floor for "near-white" outline pixels. Calibrated on the VIP Point
Rewards animated highlight: V cycles between ~240 and ~245 across the visible
phases; 200 leaves headroom for the darkest sampled frame while still
rejecting mid-tone bright pixels (cyan card edges sit at V≈225)."""

WHITE_FRAME_NEAR_WHITE_MAX_SATURATION = 80
"""HSV S ceiling for "near-white" outline pixels. The animated highlight halo
sits at S≈90-100 on the outer ring but the outline itself averages well under
80 in mask sampling. Yellow / orange UI panels start at S≈140."""

WHITE_FRAME_MORPH_CLOSE_KERNEL = 5
"""Morphological closing kernel side (pixels). Bridges 1-2 px gaps where the
animated outline aliases against the cyan card or briefly dips below the V
floor mid-frame. Going larger merges unrelated bright UI elements."""

WHITE_FRAME_MIN_SIDE_PX = 30
WHITE_FRAME_MAX_SIDE_PX = 300
"""Bounding-rect side limits. Below 30 px we pick up text characters and lock
badges; above 300 we start merging the entire popup card."""

WHITE_FRAME_MIN_ASPECT = 0.4
WHITE_FRAME_MAX_ASPECT = 2.5
"""``max(w,h)/min(w,h)`` bounds — keeps roughly square or modestly elongated
shapes (icons, claim buttons) and rejects long thin strips like header bars."""

WHITE_FRAME_MAX_FILL_RATIO = 0.7
"""``contour_area / bounding_rect_area``. A hollow outline fills ≤ 0.3 of its
bbox; solid bright blobs (text glyphs, plain bright panels) fill closer to
0.9. 0.7 cleanly separates outlines from solids while tolerating gappy outline
detections."""

WHITE_FRAME_MIN_INTERIOR_SATURATION = 70.0
"""After geometry passes, the centered 60% of the bounding rect must average
``S ≥ 70`` — there must be a *colored* icon inside the white frame. Lock /
checkmark badges have interior S ≈ 42-46 (washed-out gold). Claimable purple
icons sit at S ≈ 117. The 70 floor cleanly bisects the gap."""

WHITE_FRAME_MERGE_GAP_PX = 5
"""When the outline detection produces two adjacent contour fragments (top
and bottom strips that ``morph close`` didn't merge), treat them as one
candidate if their bounding rects sit within this many pixels of each other."""


def _pct_bbox_to_px_rect_clipped(
    bbox_percent: dict[str, float] | None,
    image_w: int,
    image_h: int,
) -> tuple[int, int, int, int]:
    """Convert ``bbox_percent`` to ``(L, T, R, B)`` clipped to image bounds.
    ``None`` returns the full-image rect."""
    if not isinstance(bbox_percent, dict):
        return 0, 0, image_w, image_h
    if not all(k in bbox_percent for k in ("x", "y", "width", "height")):
        return 0, 0, image_w, image_h
    return _pct_bbox_to_px_rect(bbox_percent, image_w, image_h)


def find_white_border_match_in_search_roi(
    image_bgr: np.ndarray,
    search_bbox_percent: dict[str, float] | None = None,
    *,
    near_white_min_value: int = WHITE_FRAME_NEAR_WHITE_MIN_VALUE,
    near_white_max_saturation: int = WHITE_FRAME_NEAR_WHITE_MAX_SATURATION,
    morph_close_kernel: int = WHITE_FRAME_MORPH_CLOSE_KERNEL,
    min_side_px: int = WHITE_FRAME_MIN_SIDE_PX,
    max_side_px: int = WHITE_FRAME_MAX_SIDE_PX,
    min_aspect: float = WHITE_FRAME_MIN_ASPECT,
    max_aspect: float = WHITE_FRAME_MAX_ASPECT,
    max_fill_ratio: float = WHITE_FRAME_MAX_FILL_RATIO,
    min_interior_saturation: float = WHITE_FRAME_MIN_INTERIOR_SATURATION,
    merge_gap_px: int = WHITE_FRAME_MERGE_GAP_PX,
) -> dict[str, object] | None:
    """Find a near-white closed rectangular outline inside ``search_bbox_percent``.

    Returns a dict with the matched bbox + center in percent-of-frame coords,
    plus interior stats for debug — or ``None`` if no candidate passes the
    geometry + interior filters.

    Steps:

    1. HSV mask near-white pixels (high V, low S) inside the search ROI.
    2. ``cv2.MORPH_CLOSE`` to bridge tiny outline gaps.
    3. Filter contours by side length, aspect ratio, hollowness.
    4. Merge adjacent/overlapping contour bounding rects (single outline
       sometimes splits into two strips).
    5. Verify each merged candidate has a saturated icon body inside.
    6. Return the largest-area candidate (the most-complete outline detection).
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return None
    hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    if hi <= 0 or wi <= 0:
        return None

    L, T, R, B = _pct_bbox_to_px_rect_clipped(search_bbox_percent, wi, hi)
    if R <= L or B <= T:
        return None
    roi = image_bgr[T:B, L:R]
    if roi.size == 0:
        return None

    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = (
        (hsv[..., 2] >= int(near_white_min_value))
        & (hsv[..., 1] <= int(near_white_max_saturation))
    ).astype(np.uint8) * 255
    if morph_close_kernel > 0:
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (int(morph_close_kernel), int(morph_close_kernel))
        )
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    rects: list[tuple[int, int, int, int, float]] = []
    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        if w < int(min_side_px) or h < int(min_side_px):
            continue
        if w > int(max_side_px) or h > int(max_side_px):
            continue
        long_side = max(w, h)
        short_side = max(1, min(w, h))
        aspect_lo_ok = float(min(w, h)) / float(max(w, h)) >= float(min_aspect)
        aspect_hi_ok = float(long_side) / float(short_side) <= float(max_aspect)
        if not (aspect_lo_ok and aspect_hi_ok):
            continue
        area = float(cv2.contourArea(cnt))
        if (w * h) <= 0:
            continue
        if area / float(w * h) > float(max_fill_ratio):
            continue
        rects.append((x, y, w, h, area))

    if not rects:
        return None

    # Merge near-overlapping rects (single outline may split into two strips).
    merged: list[tuple[int, int, int, int]] = []
    used = [False] * len(rects)
    for i, (x, y, w, h, _) in enumerate(rects):
        if used[i]:
            continue
        cx1, cy1, cx2, cy2 = x, y, x + w, y + h
        used[i] = True
        changed = True
        while changed:
            changed = False
            for j, (x2, y2, w2, h2, _) in enumerate(rects):
                if used[j]:
                    continue
                gap = int(merge_gap_px)
                if not (
                    x2 + w2 + gap < cx1
                    or x2 - gap > cx2
                    or y2 + h2 + gap < cy1
                    or y2 - gap > cy2
                ):
                    cx1 = min(cx1, x2)
                    cy1 = min(cy1, y2)
                    cx2 = max(cx2, x2 + w2)
                    cy2 = max(cy2, y2 + h2)
                    used[j] = True
                    changed = True
        merged.append((cx1, cy1, cx2 - cx1, cy2 - cy1))

    best: dict[str, object] | None = None
    best_area = -1.0
    for x, y, w, h in merged:
        # Interior sample (centered 60% in ROI-local coords).
        mh = max(2, int(h * 0.2))
        mw = max(2, int(w * 0.2))
        ix0 = x + mw
        iy0 = y + mh
        ix1 = x + w - mw
        iy1 = y + h - mh
        if ix1 <= ix0 or iy1 <= iy0:
            continue
        inner = roi[iy0:iy1, ix0:ix1]
        if inner.size == 0:
            continue
        ihsv = cv2.cvtColor(inner, cv2.COLOR_BGR2HSV)
        inner_s = float(ihsv[..., 1].mean())
        inner_v = float(ihsv[..., 2].mean())
        if inner_s < float(min_interior_saturation):
            continue
        # Convert ROI-local px back to absolute image px, then to percent.
        gx = x + L
        gy = y + T
        cx_pct = 100.0 * (gx + w / 2.0) / wi
        cy_pct = 100.0 * (gy + h / 2.0) / hi
        candidate: dict[str, object] = {
            "bbox_percent": {
                "x": 100.0 * gx / wi,
                "y": 100.0 * gy / hi,
                "width": 100.0 * w / wi,
                "height": 100.0 * h / hi,
            },
            "px_rect": (int(gx), int(gy), int(w), int(h)),
            "cx_pct": cx_pct,
            "cy_pct": cy_pct,
            "interior_saturation": inner_s,
            "interior_value": inner_v,
        }
        if w * h > best_area:
            best_area = float(w * h)
            best = candidate

    return best


def _pct_bbox_to_px_rect(
    bbox_percent: dict[str, float],
    image_w: int,
    image_h: int,
) -> tuple[int, int, int, int]:
    """Convert ``bbox_percent`` to ``(L, T, R, B)`` with the same rounding as
    :func:`layout.template_match.patch_bgr_from_bbox_percent`."""
    left = bbox_percent["x"] / 100.0 * image_w
    top = bbox_percent["y"] / 100.0 * image_h
    width = bbox_percent["width"] / 100.0 * image_w
    height = bbox_percent["height"] / 100.0 * image_h

    L = int(math.floor(left))
    T = int(math.floor(top))
    R = int(math.ceil(left + width))
    B = int(math.ceil(top + height))

    L = max(0, min(L, image_w - 1))
    T = max(0, min(T, image_h - 1))
    R = max(L + 1, min(R, image_w))
    B = max(T + 1, min(B, image_h))
    return L, T, R, B


def white_border_halo_stats(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
    *,
    halo_px: int = WHITE_BORDER_HALO_PX,
    interior_sample_pct: float = WHITE_BORDER_INTERIOR_SAMPLE_PCT,
) -> tuple[float, float, float, int]:
    """Return ``(halo_mean_saturation, halo_mean_value, interior_mean_saturation,
    ring_pixel_count)`` for the halo ring just outside ``bbox_percent`` and the
    centered interior fraction of the bbox.

    The halo is the set of pixels in the rectangle grown by ``halo_px`` on
    each side, minus the original bbox interior. The interior sample is the
    centered ``interior_sample_pct`` of the bbox (trimming the outer band to
    avoid picking up halo bleed). Clipped to the image bounds.
    Returns ``(0.0, 0.0, 0.0, 0)`` for empty / malformed inputs — callers
    should check the pixel count before trusting the means.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return 0.0, 0.0, 0.0, 0
    if not isinstance(bbox_percent, dict):
        return 0.0, 0.0, 0.0, 0
    if not all(k in bbox_percent for k in ("x", "y", "width", "height")):
        return 0.0, 0.0, 0.0, 0
    if halo_px <= 0:
        return 0.0, 0.0, 0.0, 0

    hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    if hi <= 0 or wi <= 0:
        return 0.0, 0.0, 0.0, 0

    L, T, R, B = _pct_bbox_to_px_rect(bbox_percent, wi, hi)
    L_out = max(0, L - halo_px)
    T_out = max(0, T - halo_px)
    R_out = min(wi, R + halo_px)
    B_out = min(hi, B + halo_px)
    if R_out <= L_out or B_out <= T_out:
        return 0.0, 0.0, 0.0, 0

    outer = image_bgr[T_out:B_out, L_out:R_out]
    hsv = cv2.cvtColor(outer, cv2.COLOR_BGR2HSV)

    mask = np.ones(outer.shape[:2], dtype=bool)
    li = max(0, L - L_out)
    ti = max(0, T - T_out)
    ri = min(outer.shape[1], li + (R - L))
    bi = min(outer.shape[0], ti + (B - T))
    if ri > li and bi > ti:
        mask[ti:bi, li:ri] = False

    ring_count = int(mask.sum())
    if ring_count == 0:
        return 0.0, 0.0, 0.0, 0
    halo_s = float(hsv[..., 1][mask].mean())
    halo_v = float(hsv[..., 2][mask].mean())

    bw = R - L
    bh = B - T
    trim_w = max(2, int(bw * (1.0 - float(interior_sample_pct)) / 2.0))
    trim_h = max(2, int(bh * (1.0 - float(interior_sample_pct)) / 2.0))
    inner_L = L + trim_w
    inner_T = T + trim_h
    inner_R = R - trim_w
    inner_B = B - trim_h
    if inner_R <= inner_L or inner_B <= inner_T:
        return halo_s, halo_v, 0.0, ring_count
    inner_bgr = image_bgr[inner_T:inner_B, inner_L:inner_R]
    inner_hsv = cv2.cvtColor(inner_bgr, cv2.COLOR_BGR2HSV)
    inner_s = float(inner_hsv[..., 1].mean())
    return halo_s, halo_v, inner_s, ring_count


def has_white_border_in_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
    *,
    halo_px: int = WHITE_BORDER_HALO_PX,
    max_mean_saturation: float = WHITE_BORDER_MAX_MEAN_SATURATION,
    min_mean_value: float = WHITE_BORDER_MIN_MEAN_VALUE,
    min_interior_saturation_excess: float = WHITE_BORDER_MIN_INTERIOR_SATURATION_EXCESS,
    min_ring_pixels: int = WHITE_BORDER_MIN_RING_PIXELS,
) -> bool:
    """Return ``True`` iff the bbox is surrounded by a near-white outline.

    Three gates must pass:

    1. halo mean saturation below ``max_mean_saturation`` (halo is near-white);
    2. halo mean value above ``min_mean_value`` (halo is bright, not dark grey);
    3. interior mean saturation exceeds halo by at least
       ``min_interior_saturation_excess`` (there is a colored icon body inside,
       not just continuous light UI background).

    The halo must also contain at least ``min_ring_pixels`` to make the mean
    statistically meaningful — clipped bbox at the image edge with only a
    sliver of halo returns ``False`` rather than guessing.
    """
    halo_s, halo_v, inner_s, ring_count = white_border_halo_stats(
        image_bgr, bbox_percent, halo_px=halo_px
    )
    if ring_count < int(min_ring_pixels):
        return False
    if halo_s >= float(max_mean_saturation):
        return False
    if halo_v <= float(min_mean_value):
        return False
    if (inner_s - halo_s) < float(min_interior_saturation_excess):
        return False
    return True
