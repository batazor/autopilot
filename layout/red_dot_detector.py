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
RED_DOT_RADIUS_PX_MAX_AT_REF = 22
"""Min/max badge radius at ``REFERENCE_IMAGE_HEIGHT``. Scaled per-call."""

RED_DOT_MIN_CIRCULARITY = 0.55
"""``4 * pi * area / perimeter**2`` — 1.0 is a perfect circle. Aliasing of small
badges keeps real-world values in the 0.6–0.85 band; 0.55 leaves headroom."""

RED_DOT_MIN_FILL_RATIO = 0.55
"""Contour area / bbox area. A circle inscribed in a square fills ~78%; the
bound is loose to absorb digit overlay and 1-px halos."""

RED_DOT_MAX_ASPECT = 1.5
"""Bbox aspect (max(w,h)/min(w,h)). Real badges are ≈1; banners are ≥3."""

RED_DOT_MIN_MEDIAN_SATURATION = 180
RED_DOT_MIN_MEDIAN_VALUE = 200
"""Median S/V (HSV) of the matched red pixels inside the contour. Real
notification badges are pure saturated red (S≈198–225, V≈216–255). Pinkish or
orange-leaning blobs (cooked-meat icons, salmon UI elements) sit at S≈170,
V≈204 — these floors discriminate cleanly. Sampling restricted to mask pixels
so an inner white counter digit cannot drag the medians down."""


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
    lo1 = np.array([0, 110, 90], dtype=np.uint8)
    hi1 = np.array([10, 255, 255], dtype=np.uint8)
    lo2 = np.array([170, 110, 90], dtype=np.uint8)
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

        circularity = 4.0 * pi * area / (perimeter * perimeter)
        if circularity < RED_DOT_MIN_CIRCULARITY:
            continue

        bbox_area = bw * bh
        fill_ratio = area / bbox_area
        if fill_ratio < RED_DOT_MIN_FILL_RATIO:
            continue

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
    onto it. Instead we require that **both** signature pixel populations
    cross their floors simultaneously:

    * bright icy-cyan ≥ :data:`FROST_BADGE_CYAN_MIN_RATIO` of patch pixels
      (the badge body / halo);
    * magenta sparkles ≥ :data:`FROST_BADGE_PINK_MIN_RATIO` of patch pixels
      (the snow-particle glitter exclusive to event indicators).

    The conjunction is what makes this safe: a plain blue button or the sky
    have lots of cyan but ~0 pink, so they do not register.
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
        (H >= cy_lo)
        & (H <= cy_hi)
        & (S >= FROST_BADGE_CYAN_MIN_SAT)
        & (V >= FROST_BADGE_CYAN_MIN_VAL)
    )
    if float(cyan.sum()) / total < FROST_BADGE_CYAN_MIN_RATIO:
        return False

    pk_lo, pk_hi = FROST_BADGE_PINK_HUE_RANGE
    pink = (
        (H >= pk_lo)
        & (H <= pk_hi)
        & (S >= FROST_BADGE_PINK_MIN_SAT)
        & (V >= FROST_BADGE_PINK_MIN_VAL)
    )
    return float(pink.sum()) / total >= FROST_BADGE_PINK_MIN_RATIO


def has_red_dot_in_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
    *,
    pad_px: int = 2,
    accept_frost: bool = True,
) -> bool:
    """Return True iff ``bbox_percent`` contains at least one event indicator.

    Two detector backends are considered:

    * :func:`find_red_dots` for classic bright-red circular badges;
    * :func:`has_frost_badge` for the winter-event icy-cyan capsule variant
      (enabled by default, opt out via ``accept_frost=False`` for callers
      that need strict red-only semantics).

    A small ``pad_px`` margin absorbs single-pixel rounding when a badge sits
    at the very edge of the labeled region.
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
    return False
