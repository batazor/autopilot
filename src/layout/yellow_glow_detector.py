"""Detect the yellow / golden glow that marks a *claimable* tile.

Some shop pages (dawn_fund, growth packs, …) stack rewards in a vertical
column. Locked tiles render with a flat purple background; the next
claimable tile gets a saturated yellow-to-orange rim around it. The colour
is a thin contour around the tile, so detection is a per-pixel HSV scan
inside the tile's bbox plus a "what fraction lit up?" threshold — the
locked tiles measure ~0 % glow, the claimable one ~4 %.

Two entry points:

* :func:`has_yellow_glow_in_bbox_percent` — boolean, mirrors the
  :func:`layout.red_dot_detector.has_red_dot_in_bbox_percent` shape so
  overlay-engine and DSL rules can wire it the same way.
* :func:`find_glowing_slots_in_grid` — given a column bbox and N vertical
  slots, returns which slots are glowing (index + per-slot bbox in % so
  the bot can click them).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from layout.template_match import patch_bgr_from_bbox_percent

if TYPE_CHECKING:
    pass


GLOW_HUE_MIN = 5
GLOW_HUE_MAX = 45
"""Warm-tone hue band covering the yellow-through-orange rim. Avoids the
shop's saturated blue background (H≈110) and red notification badges (H≈0)
with comfortable margin on both sides."""

GLOW_MIN_SATURATION = 60
GLOW_MIN_VALUE = 150
"""Brightness + saturation floor — rejects washed-out backgrounds, locked
tile inner shadows, and faint UI gradients that drift toward yellow."""

GLOW_MIN_PIXEL_RATIO = 0.005
"""Minimum fraction of patch pixels that must clear the HSV gates to count
as glowing. On dawn_fund: claimable slot measures 0.043, locked slots
measure 0.000 — the 0.005 floor sits inside that gap and leaves room for
partially-occluded tiles."""

SQUARE_GLOW_CLOSE_KERNEL = 11
"""Morphological-close kernel side (px) used to merge a glowing rim's
broken corners into a single connected component before measuring shape."""

SQUARE_GLOW_MIN_SIDE = 80
SQUARE_GLOW_MAX_SIDE = 115
"""Bounding-box size band (px) for square claim tiles. The shop chest grid
uses a uniform tile size of ~92×92 across all three columns (Free, Mid,
Epic), so the band is tight enough to exclude both small badges and the
larger decorative "Claimable" indicator chest in the page header
(~150×150). Widen the band for screens that use a different tile size."""

SQUARE_GLOW_MIN_ASPECT = 0.85
SQUARE_GLOW_MAX_ASPECT = 1.15
"""Width/height ratio gate. Real claim rims are square; wide CTAs (the
``$19.99`` purchase button: 2.55:1) and tall progress badges (``Lv. 6``:
0.79:1) fail this gate and don't pollute the result."""

SQUARE_GLOW_MAX_FILL_RATIO = 0.45
"""Original-yellow pixels / bbox-area threshold. A hollow rim fills 8-43 %
of its bbox; a solid icon (chest body, lock badge, $19.99 button surface)
fills 55 %+. The 0.45 ceiling rejects the solid blobs while keeping
gold-bordered indicators."""

SQUARE_GLOW_BORDER_RING_PX = 4
SQUARE_GLOW_MAX_BORDER_SATURATION = 130
"""Some claim tiles render with a *filled* warm body (e.g. dawn_fund's
big-diamond middle column: 1000/1500/2500/3500). Their fill ratio sits at
~0.55 — above the hollow-rim threshold — so the fill gate alone rejects
them. The claimable variant is set apart by a brighter cream/pale-gold
outline (median border S≈106); the locked variants keep the darker
saturated-orange body all the way out (median border S≈156). A 50-point
gap allows a comfortable 130 floor."""


@dataclass(frozen=True)
class GlowSquare:
    """One claim-rim candidate found by full-image scan.

    ``bbox_percent`` is in screen % (x, y, width, height) so the bot can
    tap inside it without re-measuring; ``fill_ratio`` is how solid the
    blob is (low = hollow rim, high = solid icon) and is exposed for
    callers that want to rank candidates beyond the binary threshold.
    """

    bbox_percent: dict[str, float]
    fill_ratio: float
    aspect: float


@dataclass(frozen=True)
class GlowingSlot:
    """One vertical slot in a prize grid + whether its rim is glowing.

    ``index`` is 0-based top-to-bottom; ``bbox_percent`` is in screen %
    (same convention as area.yaml) so the bot can tap inside it.
    """

    index: int
    bbox_percent: dict[str, float]
    glow_ratio: float
    is_claimable: bool


def yellow_glow_pixel_ratio(patch_bgr: np.ndarray) -> float:
    """Fraction of pixels in ``patch_bgr`` that clear the warm-rim HSV gates."""
    if patch_bgr is None or patch_bgr.ndim != 3 or patch_bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch_bgr, cv2.COLOR_BGR2HSV)
    h_plane, s_plane, v_plane = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = (
        (h_plane >= GLOW_HUE_MIN)
        & (h_plane <= GLOW_HUE_MAX)
        & (s_plane >= GLOW_MIN_SATURATION)
        & (v_plane >= GLOW_MIN_VALUE)
    )
    return float(mask.mean())


def has_yellow_glow_in_bbox_percent(
    image_bgr: np.ndarray,
    bbox_percent: dict[str, float],
    *,
    min_ratio: float = GLOW_MIN_PIXEL_RATIO,
) -> bool:
    """``True`` iff the labeled bbox shows a yellow / golden claim rim."""
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return False
    if not isinstance(bbox_percent, dict):
        return False
    if not all(k in bbox_percent for k in ("x", "y", "width", "height")):
        return False
    patch, _ = patch_bgr_from_bbox_percent(image_bgr, bbox_percent)
    if patch.size == 0:
        return False
    return yellow_glow_pixel_ratio(patch) >= float(min_ratio)


def _border_median_saturation(
    image_bgr: np.ndarray, x: int, y: int, w: int, h: int, *, ring_px: int
) -> float:
    """Median HSV saturation of pixels in a ``ring_px``-wide inner border
    of ``(x, y, w, h)``. Used to tell a bright cream rim (low S) from a
    saturated orange body (high S) when both score similar fill ratios."""
    img_h, img_w = image_bgr.shape[:2]
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(img_w, x + w)
    y1 = min(img_h, y + h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    patch = image_bgr[y0:y1, x0:x1]
    ph, pw = patch.shape[:2]
    r = max(1, min(ring_px, ph // 2, pw // 2))
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    border_mask = np.zeros(patch.shape[:2], dtype=bool)
    border_mask[:r, :] = True
    border_mask[-r:, :] = True
    border_mask[:, :r] = True
    border_mask[:, -r:] = True
    if not border_mask.any():
        return 0.0
    return float(np.median(hsv[border_mask, 1]))


def _yellow_mask(image_bgr: np.ndarray) -> np.ndarray:
    """Binary mask of pixels passing the warm-rim HSV gates."""
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    return (
        (hsv[..., 0] >= GLOW_HUE_MIN)
        & (hsv[..., 0] <= GLOW_HUE_MAX)
        & (hsv[..., 1] >= GLOW_MIN_SATURATION)
        & (hsv[..., 2] >= GLOW_MIN_VALUE)
    ).astype(np.uint8) * 255


def find_yellow_glow_squares(
    image_bgr: np.ndarray,
    *,
    min_side_px: int = SQUARE_GLOW_MIN_SIDE,
    max_side_px: int = SQUARE_GLOW_MAX_SIDE,
    min_aspect: float = SQUARE_GLOW_MIN_ASPECT,
    max_aspect: float = SQUARE_GLOW_MAX_ASPECT,
    max_fill_ratio: float = SQUARE_GLOW_MAX_FILL_RATIO,
) -> list[GlowSquare]:
    """Find every square-aspect yellow rim on the full frame.

    Use when the bot doesn't have a pre-annotated bbox to scope into —
    e.g. detecting all claimable indicators on a shop page at once.
    Returns a (possibly empty) list of :class:`GlowSquare`, sorted by
    fill ratio ascending so the most-clearly-hollow rims come first.

    Three independent gates separate real claim rims from incidental
    warm-tone UI:

    * **Size band** filters out tiny badges and full-page panels.
    * **Square aspect** rejects CTAs (very wide) and progress labels
      (slightly tall) that happen to be yellow.
    * **Fill ratio** rejects solid yellow icons (chest bodies, lock
      badges, ``$19.99`` button surface) by requiring the bbox interior
      to be mostly NOT-yellow — i.e. a hollow rim, not a fill.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return []
    mask = _yellow_mask(image_bgr)
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (SQUARE_GLOW_CLOSE_KERNEL, SQUARE_GLOW_CLOSE_KERNEL)
    )
    closed = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    num, _, stats, _ = cv2.connectedComponentsWithStats(closed, connectivity=8)

    img_h, img_w = image_bgr.shape[:2]
    out: list[GlowSquare] = []
    for i in range(1, num):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        if cw < min_side_px or cw > max_side_px:
            continue
        if ch < min_side_px or ch > max_side_px:
            continue
        aspect = cw / float(ch)
        if aspect < min_aspect or aspect > max_aspect:
            continue
        # Fill ratio uses the *original* (pre-close) yellow pixels — the
        # closing fills the rim's interior with the candidate's bbox, so
        # measuring on `closed` would always look solid.
        sub = mask[y : y + ch, x : x + cw]
        if sub.size == 0:
            continue
        fill = float(sub.sum() / 255.0) / float(cw * ch)
        if fill >= max_fill_ratio:
            # Fallback: tile may be a filled warm body. Claimable variants
            # carry a brighter pale-gold rim — measured by lower median
            # saturation of the bbox border. Locked tiles stay saturated
            # orange all the way out.
            border_sat = _border_median_saturation(
                image_bgr, x, y, cw, ch, ring_px=SQUARE_GLOW_BORDER_RING_PX
            )
            if border_sat >= SQUARE_GLOW_MAX_BORDER_SATURATION:
                continue
        out.append(
            GlowSquare(
                bbox_percent={
                    "x": (x / img_w) * 100.0,
                    "y": (y / img_h) * 100.0,
                    "width": (cw / img_w) * 100.0,
                    "height": (ch / img_h) * 100.0,
                },
                fill_ratio=fill,
                aspect=aspect,
            )
        )
    out.sort(key=lambda g: g.fill_ratio)
    return out


def find_glowing_slots_in_grid(
    image_bgr: np.ndarray,
    box_bbox_percent: dict[str, float],
    *,
    n_slots: int,
    min_ratio: float = GLOW_MIN_PIXEL_RATIO,
) -> list[GlowingSlot]:
    """Split a vertical box into N equal slots and detect glow per slot.

    Tile grids in the shop (dawn_fund prize ladder, similar layouts) place
    rewards on a fixed vertical pitch; cutting the bbox into ``n_slots``
    equal stripes is enough — no need for inter-slot gap detection. The
    return value carries each slot's bbox in screen %, so the caller can
    tap the claimable one without re-measuring.
    """
    if n_slots <= 0:
        return []
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return []
    if not isinstance(box_bbox_percent, dict):
        return []
    if not all(k in box_bbox_percent for k in ("x", "y", "width", "height")):
        return []

    out: list[GlowingSlot] = []
    bx = float(box_bbox_percent["x"])
    by = float(box_bbox_percent["y"])
    bw = float(box_bbox_percent["width"])
    bh = float(box_bbox_percent["height"])
    slot_h = bh / float(n_slots)

    for i in range(n_slots):
        slot_bbox = {
            "x": bx,
            "y": by + i * slot_h,
            "width": bw,
            "height": slot_h,
        }
        patch, _ = patch_bgr_from_bbox_percent(image_bgr, slot_bbox)
        ratio = yellow_glow_pixel_ratio(patch) if patch.size > 0 else 0.0
        out.append(
            GlowingSlot(
                index=i,
                bbox_percent=slot_bbox,
                glow_ratio=ratio,
                is_claimable=ratio >= float(min_ratio),
            )
        )
    return out
