"""Programmatic red-dot indicator detector.

The game uses a small bright-red circular badge (with optional white digit) on top
of buttons / icons to signal "new event / unread notification". The detector here
finds such badges purely from pixels — no per-region template, no labeling effort.

Typical use::

    from layout.red_dot_detector import has_red_dot_in_bbox_percent
    if has_red_dot_in_bbox_percent(image_bgr, region["bbox"]):
        ...

Design notes:

* HSV thresholding (red = two hue ranges around 0 and 180); saturation / value
  floors filter dark or washed-out pixels.
* Morphological close fills the interior digit (so a bright digit on top of the
  red badge does not split the contour).
* Contour-level filters (radius range, circularity, fill ratio, aspect) ensure
  that long red banners or text underlines do not register as a "dot".
* Radius range scales with the *captured screen height* so the same constants
  work at 720×1280 and 1080×1920 BlueStacks resolutions.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import pi

import cv2
import numpy as np

from layout.template_match import patch_bgr_from_bbox_percent

REFERENCE_IMAGE_HEIGHT = 1280
"""Calibration height for the radius range (pixels)."""

RED_DOT_RADIUS_PX_MIN_AT_REF = 5
RED_DOT_RADIUS_PX_MAX_AT_REF = 15
"""Min/max badge radius at ``REFERENCE_IMAGE_HEIGHT``. Scaled per-call.

Real notification badges in the game cluster at 7.8–10 px radius at 1280
height (measured across 6 main_city_v2 badges + the 1-min speedup tile).
Two-digit counters ("12", "99") render with a contour radius near 14–14.5
because the digit overlay stretches the bbox vertically. A ceiling of 15
gives ~0.5 px headroom over that ceiling while rejecting close-X buttons
and discount stickers, which sit at r≈16–20 — visually circular and
saturated red, but semantically buttons, not notifications."""

RED_DOT_MIN_CIRCULARITY = 0.45
"""``4 * pi * area / perimeter**2`` — 1.0 is a perfect circle. Aliasing of
single-digit badges sits in the 0.6–0.85 band; a two-digit counter "11" /
"12" stretches the badge into a horizontal ellipse whose circularity drops
to 0.45–0.55. The floor is set so those still pass — false positives from
non-circular red blobs are filtered by ``RED_DOT_MIN_MEDIAN_SATURATION``
and ``RED_DOT_MIN_SURROUND_MEDIAN_SATURATION``, not by shape alone."""

RED_DOT_MIN_FILL_RATIO = 0.55
"""Contour area / bbox area. A circle inscribed in a square fills ~78%; the
bound is loose to absorb digit overlay and 1-px halos."""

RED_DOT_MAX_ASPECT = 1.9
"""Bbox aspect (max(w,h)/min(w,h)). Real badges are ≈1 for a blank dot and
~1.7 for a two-digit counter ("11" / "12" / "99"). 1.9 gives headroom
for three-digit counters too without overlapping banner / strip widths
which sit at ≥3."""

RED_DOT_COUNTER_MIN_FILL_RATIO = 0.30
RED_DOT_COUNTER_MIN_WHITE_RATIO = 0.05
RED_DOT_COUNTER_MIN_WHITE_PIXELS = 8
"""Fallback gates for red badges whose white counter digit cuts up the red mask.

Some in-game counters render as a red capsule/ring around a large white digit.
The outer contour is clearly a notification badge, but its red-only mask can
look too hollow/noisy for circularity and fill-ratio gates. We accept those only
when a compact red candidate contains enough bright low-saturation pixels inside
its bbox to explain the hollow shape as a white counter, not as a banner.
"""

RED_DOT_MIN_MEDIAN_SATURATION = 180
RED_DOT_MIN_MEDIAN_VALUE = 200
"""Median S/V (HSV) of the matched red pixels inside the contour. Real
notification badges are pure saturated red (S≈198–225, V≈216–255). Pinkish or
orange-leaning blobs (cooked-meat icons, salmon UI elements) sit at S≈170,
V≈204 — these floors discriminate cleanly. Sampling restricted to mask pixels
so an inner white counter digit cannot drag the medians down."""

RED_DOT_SURROUND_RING_PX = 4
RED_DOT_MIN_SURROUND_MEDIAN_SATURATION = 45
"""Median saturation of the thin ring just outside the candidate contour
(red-mask pixels in the ring are excluded so the dot's own halo doesn't pollute
the sample). Real notification badges sit on saturated UI elements — buttons
and icon panels — whose surround S clusters in [62, 147] across the captured
720×1280 frame. Stray red marks floating over washed-out scenery (sky, snow,
character avatars rendered against open background) drop to S≈30. The gate at
45 cleanly separates them."""

RED_DOT_WIDE_SURROUND_RING_PX = 22
"""Wider-ring fallback radius, in pixels at the reference 720×1280 frame. When
the immediate surround (4 px) fails the saturation gate, we sample a second
annulus reaching further out. Rationale: legitimate badges can sit on a small
desaturated sub-element (e.g. the silver handle of the 1-min speedup tile)
inside a saturated UI strip (the row of speedup icons next to it). The narrow
ring catches the silver handle (S≈34); the wider ring crosses into the
neighbouring saturated icons (S≈226) and confirms the badge is real. A stray
red on an avatar over open sky still fails at this radius — sky stays at
S≈30 several dozen pixels out. Scaled per-call with the captured frame
height, same as the badge radius range."""

RED_DOT_NESTING_RING_PX = 10
RED_DOT_MAX_NESTING_RED_RATIO = 0.25
"""Reject candidates embedded inside a larger red structure.

A real notification badge sits as an isolated red disc on a non-red button or
icon — its 10-pixel surround contains essentially zero other red pixels. The
"+5%" / "+12%" booster sticker rendered next to resource counters has a small
red "+" character inside a red-bordered oval: that inner "+" passes every
shape and saturation gate, but its 10 px ring is 30–45 % red because the
outer oval border sits a few pixels away. The same logic kills inner red
fragments of any larger red UI element (banners, ribbons, frames) that the
morphological close did not glue to the candidate.

Numbers calibrated at 720×1280: 6 main_city_v2 badges + the 1-min speedup
tile + the two-digit counter fixture all measure 0 % red in this ring; the
"+5%" inner "+" inside the ``isWorkers`` region measures 38 % at 8 px and
42 % at 12 px. A floor at 25 % cleanly separates them. Scaled per-call with
the captured frame height, same as the badge radius range."""


# ---------------------------------------------------------------------------
# Winter-event "frost badge" variant
# ---------------------------------------------------------------------------
#
# During winter / snow events the same notification slot is rendered as an icy
# cyan capsule with magenta sparkle particles instead of the plain red disc.
# The badge body is bright cyan (H≈100-115, high S/V) and is *always* dusted
# with pink-magenta sparkle pixels (H≈150-170, high S/V). Either signal alone
# is too common to trust — bright cyan turns up on sky background, mailbox
# halos, blue UI panels — but their *combination* in a single patch is
# essentially unique to the active frost-themed indicator. We therefore gate
# detection on **both** ratios crossing their floors at once.

FROST_BADGE_CYAN_HUE_RANGE = (85, 125)
FROST_BADGE_CYAN_MIN_SAT = 80
FROST_BADGE_CYAN_MIN_VAL = 200
FROST_BADGE_CYAN_MIN_RATIO = 0.08
"""Cyan ratio gate: ≥8% of patch pixels must be bright icy cyan."""

FROST_BADGE_PINK_HUE_RANGE = (145, 175)
FROST_BADGE_PINK_MIN_SAT = 100
FROST_BADGE_PINK_MIN_VAL = 200
FROST_BADGE_PINK_MIN_RATIO = 0.002
"""Pink-sparkle ratio gate: ≥0.2% of patch pixels must be magenta sparkles.
Sparkles are sparse but unique — main_city_v2 has 0 such pixels in any sampled
notification bbox, so even a tiny presence is highly diagnostic."""

FROST_BADGE_PINK_NEAR_CYAN_DILATE_PX = 2
FROST_BADGE_MIN_PINK_NEAR_CYAN = 5
"""Co-location gate: at least :data:`FROST_BADGE_MIN_PINK_NEAR_CYAN` pink
pixels must lie within :data:`FROST_BADGE_PINK_NEAR_CYAN_DILATE_PX` of the
largest cyan connected component.

Without this gate, the cyan + pink conjunction can mis-fire on event-icon
crops where the cyan is the *snowy main_city background* and the pink is
unrelated character detail (hair / dress edges) scattered far from the cyan.
Real frost badges glue their sparkles to the icy capsule body — the largest
cyan blob — so requiring at least a handful of pink pixels to land in that
neighborhood cleanly separates the two.

Numbers from captured frames at the 2px dilation: real
``red_dot_frost_workers`` has 36 pink pixels next to the blob; the
"1st Purchase" event icon (the original false positive) has 0. A larger
dilation (5+ px) starts capturing unrelated background pink and erodes the
gap. A floor of 5 with 2px dilation leaves wide headroom on both sides."""


@dataclass(frozen=True)
class RedDotDetection:
    """One badge candidate inside the analysed patch.

    Coordinates are *relative to the analysed patch top-left*, in pixels.
    """

    cx: float
    cy: float
    radius: float
    score: float


def _radius_range_for_image_height(image_h: int) -> tuple[float, float]:
    """Scale calibration radii to the live capture height."""
    if image_h <= 0:
        return float(RED_DOT_RADIUS_PX_MIN_AT_REF), float(RED_DOT_RADIUS_PX_MAX_AT_REF)
    scale = float(image_h) / float(REFERENCE_IMAGE_HEIGHT)
    rmin = max(2.0, RED_DOT_RADIUS_PX_MIN_AT_REF * scale)
    rmax = max(rmin + 1.0, RED_DOT_RADIUS_PX_MAX_AT_REF * scale)
    return rmin, rmax


def _red_mask(patch_bgr: np.ndarray) -> np.ndarray:
    """Bright-red HSV mask. Empty (zeros) for tiny / non-3-channel patches."""
    if patch_bgr.ndim != 3 or patch_bgr.size == 0:
        return np.zeros((0, 0), dtype=np.uint8)
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    # V floor at 160 (not 90) so dim red glow inside icons — e.g. the
    # internal light shining through the trial.box chest slats, V≈100–150 —
    # drops out of the mask and doesn't merge with overlaid notification
    # badges under the morphological close. Real badges sit at V≈216–255
    # (see RED_DOT_MIN_MEDIAN_VALUE) so this floor leaves wide headroom.
    lo1 = np.array([0, 110, 160], dtype=np.uint8)
    hi1 = np.array([10, 255, 255], dtype=np.uint8)
    lo2 = np.array([170, 110, 160], dtype=np.uint8)
    hi2 = np.array([179, 255, 255], dtype=np.uint8)
    m1 = cv2.inRange(hsv, lo1, hi1)
    m2 = cv2.inRange(hsv, lo2, hi2)
    mask = cv2.bitwise_or(m1, m2)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    return mask


def find_red_dots(
    patch_bgr: np.ndarray,
    *,
    image_h_for_norm: int | None = None,
) -> list[RedDotDetection]:
    """Return all red-dot candidates inside ``patch_bgr``.

    ``image_h_for_norm`` is the *full screen* height (used to scale radius
    bounds). When ``None``, the patch height is used — fine for full-frame
    analysis but a region patch should pass the original screen height so that
    a small ROI does not silently shrink the expected dot size.
    """
    if patch_bgr.ndim != 3 or patch_bgr.size == 0:
        return []

    norm_h = int(image_h_for_norm) if image_h_for_norm and image_h_for_norm > 0 else int(patch_bgr.shape[0])
    rmin, rmax = _radius_range_for_image_height(norm_h)

    mask = _red_mask(patch_bgr)
    if mask.size == 0:
        return []
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    sat_plane = hsv[..., 1]
    val_plane = hsv[..., 2]
    ring_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (2 * RED_DOT_SURROUND_RING_PX + 1, 2 * RED_DOT_SURROUND_RING_PX + 1),
    )
    wide_ring_px = max(
        RED_DOT_SURROUND_RING_PX + 4,
        int(round(RED_DOT_WIDE_SURROUND_RING_PX * float(norm_h) / REFERENCE_IMAGE_HEIGHT)),
    )
    wide_ring_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (2 * wide_ring_px + 1, 2 * wide_ring_px + 1),
    )
    nesting_ring_px = max(
        RED_DOT_SURROUND_RING_PX + 2,
        int(round(RED_DOT_NESTING_RING_PX * float(norm_h) / REFERENCE_IMAGE_HEIGHT)),
    )
    nesting_ring_kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (2 * nesting_ring_px + 1, 2 * nesting_ring_px + 1),
    )

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[RedDotDetection] = []
    for cnt in contours:
        area = float(cv2.contourArea(cnt))
        if area <= 0.0:
            continue
        perimeter = float(cv2.arcLength(cnt, True))
        if perimeter <= 0.0:
            continue
        x, y, w, h = cv2.boundingRect(cnt)
        if w <= 0 or h <= 0:
            continue

        bw = float(max(w, 1))
        bh = float(max(h, 1))
        aspect = max(bw, bh) / min(bw, bh)
        if aspect > RED_DOT_MAX_ASPECT:
            continue

        radius = 0.5 * (bw + bh) / 2.0
        if radius < rmin or radius > rmax:
            continue

        bbox_area = bw * bh
        fill_ratio = area / bbox_area

        # Reject orange / salmon blobs (cooked meat, smiley icons, …) that pass
        # the shape filter but sit just outside the saturated-red band.
        cmask = np.zeros(mask.shape, dtype=np.uint8)
        cv2.drawContours(cmask, [cnt], -1, 255, thickness=cv2.FILLED)
        inside = (cmask > 0) & (mask > 0)
        if int(inside.sum()) < 5:
            continue
        s_med = float(np.median(sat_plane[inside]))
        v_med = float(np.median(val_plane[inside]))
        if s_med < RED_DOT_MIN_MEDIAN_SATURATION:
            continue
        if v_med < RED_DOT_MIN_MEDIAN_VALUE:
            continue

        circularity = 4.0 * pi * area / (perimeter * perimeter)
        counter_like = False
        if circularity < RED_DOT_MIN_CIRCULARITY or fill_ratio < RED_DOT_MIN_FILL_RATIO:
            bbox_hsv = hsv[y : y + h, x : x + w]
            if bbox_hsv.size == 0:
                continue
            white_counter = (bbox_hsv[..., 1] <= 70) & (bbox_hsv[..., 2] >= 185)
            white_pixels = int(white_counter.sum())
            white_ratio = white_pixels / bbox_area
            counter_like = (
                fill_ratio >= RED_DOT_COUNTER_MIN_FILL_RATIO
                and white_pixels >= RED_DOT_COUNTER_MIN_WHITE_PIXELS
                and white_ratio >= RED_DOT_COUNTER_MIN_WHITE_RATIO
            )
            if not counter_like:
                continue

        # Surround-saturation gate: a real notification badge sits on a
        # saturated UI element (button, icon). Stray reds over washed-out
        # scenery (sky, snow, an avatar's hair) drop to S≈30 and fail.
        #
        # Two-stage check: the narrow 4 px ring catches most cases. When the
        # narrow ring is desaturated, we fall back to a wider ring (~22 px)
        # to handle the case where the dot sits on a small desaturated
        # sub-element (e.g. the silver handle of the 1-min speedup tile)
        # *inside* a saturated UI row. Stray reds over open sky still fail
        # at the wider radius — sky stays desaturated several dozen pixels
        # out, while the speedup row picks up neighbouring icons at S≈226.
        dilated = cv2.dilate(cmask, ring_kernel)
        ring = (dilated > 0) & (cmask == 0) & (mask == 0)
        if not counter_like and int(ring.sum()) >= 8:
            ring_s_med = float(np.median(sat_plane[ring]))
            if ring_s_med < RED_DOT_MIN_SURROUND_MEDIAN_SATURATION:
                wide_dilated = cv2.dilate(cmask, wide_ring_kernel)
                wide_ring = (wide_dilated > 0) & (dilated == 0) & (mask == 0)
                if int(wide_ring.sum()) >= 8:
                    wide_s_med = float(np.median(sat_plane[wide_ring]))
                    if wide_s_med < RED_DOT_MIN_SURROUND_MEDIAN_SATURATION:
                        continue
                else:
                    continue

        # Nesting gate: a real notification badge sits as an isolated red disc.
        # An inner red fragment of a larger red sticker / banner / oval (e.g.
        # the "+" inside a "+5%" booster pill, or pieces of a discount ribbon
        # that morph-close did not glue to this candidate) has *other* red mass
        # a few pixels away. Counting red pixels in the immediate vicinity —
        # excluding the candidate's own contour — separates the two cleanly.
        nest_dilated = cv2.dilate(cmask, nesting_ring_kernel)
        nest_ring = (nest_dilated > 0) & (cmask == 0)
        nest_area = int(nest_ring.sum())
        if nest_area >= 8:
            nest_red = int((nest_ring & (mask > 0)).sum())
            if nest_red / float(nest_area) > RED_DOT_MAX_NESTING_RED_RATIO:
                continue

        cx = float(x) + bw / 2.0
        cy = float(y) + bh / 2.0
        score = float(min(1.0, circularity * fill_ratio))
        out.append(RedDotDetection(cx=cx, cy=cy, radius=float(radius), score=score))

    out.sort(key=lambda d: d.score, reverse=True)
    return out


def has_frost_badge(patch_bgr: np.ndarray) -> bool:
    """Return True iff ``patch_bgr`` shows the winter-event frost-themed badge.

    Statistical, not geometric: the frost overlay smears the badge silhouette
    so contour-based shape rules (used by :func:`find_red_dots`) cannot lock
    onto it. Three gates must pass:

    * bright icy-cyan ≥ :data:`FROST_BADGE_CYAN_MIN_RATIO` of patch pixels
      (the badge body / halo);
    * magenta sparkles ≥ :data:`FROST_BADGE_PINK_MIN_RATIO` of patch pixels
      (the snow-particle glitter exclusive to event indicators);
    * pink sparkles must **co-locate** with the largest cyan blob (the
      capsule body) — see :data:`FROST_BADGE_MIN_PINK_NEAR_CYAN`. Without
      this, the cyan+pink conjunction can fire when cyan is the *snowy
      main_city background* and pink pixels are scattered character detail.
    """

    if patch_bgr is None or patch_bgr.ndim != 3 or patch_bgr.size == 0:
        return False
    h, w = int(patch_bgr.shape[0]), int(patch_bgr.shape[1])
    if h <= 0 or w <= 0:
        return False
    total = float(h * w)

    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[..., 0], hsv[..., 1], hsv[..., 2]

    cy_lo, cy_hi = FROST_BADGE_CYAN_HUE_RANGE
    cyan = (
        (cy_lo <= H)
        & (cy_hi >= H)
        & (S >= FROST_BADGE_CYAN_MIN_SAT)
        & (V >= FROST_BADGE_CYAN_MIN_VAL)
    )
    if float(cyan.sum()) / total < FROST_BADGE_CYAN_MIN_RATIO:
        return False

    pk_lo, pk_hi = FROST_BADGE_PINK_HUE_RANGE
    pink = (
        (pk_lo <= H)
        & (pk_hi >= H)
        & (S >= FROST_BADGE_PINK_MIN_SAT)
        & (V >= FROST_BADGE_PINK_MIN_VAL)
    )
    if float(pink.sum()) / total < FROST_BADGE_PINK_MIN_RATIO:
        return False

    # Co-location gate: pink sparkles must lie next to the cyan capsule, not
    # be scattered far from it. The largest cyan connected component is the
    # capsule body; sparkles are within a few pixels of it.
    cyan_u8 = cyan.astype(np.uint8) * 255
    num_cc, labels, stats_cc, _ = cv2.connectedComponentsWithStats(cyan_u8, connectivity=8)
    if num_cc <= 1:
        return False
    largest_idx = 1 + int(stats_cc[1:, cv2.CC_STAT_AREA].argmax())
    largest_blob_u8 = ((labels == largest_idx).astype(np.uint8)) * 255
    kernel_side = 2 * FROST_BADGE_PINK_NEAR_CYAN_DILATE_PX + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_side, kernel_side))
    blob_neighborhood = cv2.dilate(largest_blob_u8, kernel) > 0
    pink_near_blob = int((pink & blob_neighborhood).sum())
    return pink_near_blob >= FROST_BADGE_MIN_PINK_NEAR_CYAN


def has_red_dot_in_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
    *,
    pad_px: int = 2,
    edge_badge_pad_ratio: float = 0.85,
    accept_frost: bool = True,
) -> bool:
    """Return True iff ``bbox_percent`` contains at least one event indicator.

    Two detector backends are considered:

    * :func:`find_red_dots` for classic bright-red circular badges;
    * :func:`has_frost_badge` for the winter-event icy-cyan capsule variant
      (enabled by default, opt out via ``accept_frost=False`` for callers
      that need strict red-only semantics).

    A small ``pad_px`` margin absorbs single-pixel rounding when a badge sits
    at the very edge of the labeled region. If that misses, a bounded
    edge-badge fallback expands mostly upward: unread counters such as ``60``
    are wider than a dot and often hang above the icon's labeled bbox.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return False
    if not isinstance(bbox_percent, dict):
        return False
    if not all(k in bbox_percent for k in ("x", "y", "width", "height")):
        return False

    hi, wi = int(image_bgr.shape[0]), int(image_bgr.shape[1])
    if hi <= 0 or wi <= 0:
        return False

    patch, (L, T) = patch_bgr_from_bbox_percent(image_bgr, bbox_percent)
    if len(find_red_dots(patch, image_h_for_norm=hi)) > 0:
        return True
    if accept_frost and has_frost_badge(patch):
        return True

    if pad_px > 0:
        L2 = max(0, L - pad_px)
        T2 = max(0, T - pad_px)
        R2 = min(wi, L + int(patch.shape[1]) + pad_px)
        B2 = min(hi, T + int(patch.shape[0]) + pad_px)
        patch = image_bgr[T2:B2, L2:R2]

    if len(find_red_dots(patch, image_h_for_norm=hi)) > 0:
        return True
    if accept_frost and has_frost_badge(patch):
        return True

    # Counter badges can sit just above the icon region. Keep this fallback
    # local to the bbox so adjacent UI notifications do not leak in.
    if edge_badge_pad_ratio > 0.0:
        patch_h = int(patch.shape[0])
        patch_w = int(patch.shape[1])
        top_extra = max(pad_px, int(round(patch_h * edge_badge_pad_ratio)))
        side_extra = max(pad_px, int(round(patch_w * 0.25)))
        L3 = max(0, L - side_extra)
        T3 = max(0, T - top_extra)
        R3 = min(wi, L + patch_w + side_extra)
        B3 = min(hi, T + patch_h + pad_px)
        if T3 < T or L3 < L or R3 > L + patch_w:
            expanded = image_bgr[T3:B3, L3:R3]
            if len(find_red_dots(expanded, image_h_for_norm=hi)) > 0:
                return True
            if accept_frost and has_frost_badge(expanded):
                return True

    return False
