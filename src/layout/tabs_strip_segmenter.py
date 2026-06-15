"""Programmatic tab-strip segmenter.

Some game screens (shop, backpack, …) render a horizontal strip of tabs where
the selected tab is drawn as a light cream / white capsule and the others as
a saturated mid-blue. The X position of every tab shifts with the active one
because the selected capsule is slightly wider — so per-tab static bboxes in
``area.yaml`` give wrong red-dot results as soon as the user changes tab.

This detector segments the strip dynamically:

1. Locate the active tab by finding the largest "white" connected component
   in the strip's middle Y band (the body of the active capsule; text labels
   at the bottom of inactive tabs are excluded by the Y crop).
2. Use the active capsule width as the tab pitch and walk left/right from
   its center, dropping slots whose column-content ratio collapses (off-screen
   or end of strip).
3. For each slot, classify ``active`` via :func:`is_tab_active_in_bbox_percent`
   and ``has_red_dot`` via :func:`find_red_dots` on the upper-right quadrant.
4. Return tabs sorted left-to-right with ``index`` and bbox in percent.

Yellow active-tab variants (Trials-style) are not anchored here because the
white-blob anchor only fires on the cream/white variant; callers needing
that should fall back to per-region rules.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from layout.red_dot_detector import find_red_dots
from layout.tab_active_detector import is_tab_active_in_bbox_percent
from layout.template_match import patch_bgr_from_bbox_percent

ACTIVE_WHITE_MAX_SAT = 60
ACTIVE_WHITE_MIN_VAL = 200
"""HSV gates for the active-tab capsule body (low S, high V)."""

ACTIVE_BAND_Y_LO = 0.20
ACTIVE_BAND_Y_HI = 0.60
"""Middle Y slice (fraction of strip height) used to anchor the active blob.

The slice excludes the strip's top edge halo and the white text labels that
sit at the bottom of inactive tabs — both would otherwise leak into the
"largest white CC" and inflate the active bbox to the full strip width."""

MIN_ACTIVE_AREA_RATIO = 0.005
"""Reject candidate active blobs smaller than this fraction of the strip area."""

MIN_ACTIVE_WIDTH_RATIO = 0.20
"""If the largest white CC is at least this wide (fraction of strip width), trust
it as-is. Otherwise the capsule is presumed fragmented by its centre icon and
we retry the connected-component pass with a small horizontal closing kernel
(:data:`ACTIVE_CLOSE_KERNEL_W`). Calibrated on shop refs: intact capsules
measure 27-30 % of the strip; daily_deals fragments to 17 % when the chest
icon punches through the middle Y band."""

ACTIVE_CLOSE_KERNEL_W = 5
"""Horizontal closing kernel width (px) used as a *fallback* when the no-close
CC pass returns a too-narrow active candidate. Kept small so it bridges
intra-capsule gaps without merging the capsule with neighbouring white blobs
(e.g. the round "!" badge that sits a few px outside the capsule on some
shop pages — a wider kernel would glue them together)."""

PITCH_SCALE = 1.05
"""Tab pitch = active capsule width × this. The white capsule blob is detected
slightly narrower than the actual click target (rounded corners trim the
right edge a few px), so the true tab-to-tab spacing is ~5% wider than the
measured active width. Calibrated against shop v1: dots at strip-x 209/408/608
(spacing 199-200) vs measured active_w=192."""

CONTENT_MIN_COL_RATIO = 0.20
"""Stop walking outward when a slot's mean column-content ratio drops below
this — signals we walked off the visible tab strip."""

FALLBACK_BLUE_HUE_LO = 85
FALLBACK_BLUE_HUE_HI = 120
FALLBACK_BLUE_MIN_SAT = 80
FALLBACK_BLUE_MIN_VAL = 100
FALLBACK_BAND_Y_HI = 0.25
FALLBACK_COL_RATIO = 0.10
FALLBACK_SMOOTH_KERNEL_W = 9
"""Fallback segmentation for shop-like strips with no reliable active capsule.

Some shop pages render all visible top-strip tabs as blue capsules while the
actual active product page is represented by the content panel, not by a white
tab. The active-blob anchor then latches onto a tiny piece of white text and
produces many skinny slots. In that case, segment visible blue tab bodies in
the top quarter of the strip instead.
"""


@dataclass(frozen=True)
class TabDetection:
    """One detected tab inside the strip.

    ``index`` is 0-based left-to-right. ``bbox_percent`` is relative to the
    full screen (same convention as area.yaml regions). ``active`` reflects
    the standard tab-active detector (handles both light and yellow variants);
    ``has_red_dot`` is True iff any classic red badge sits in the tab's
    upper-right quadrant.
    """

    index: int
    bbox_percent: dict[str, float]
    active: bool
    has_red_dot: bool
    color_state: str = "unknown"
    segment_source: str = "unknown"


def _largest_white_blob(band: np.ndarray, min_area: float) -> tuple[int, int] | None:
    """Return ``(x, width)`` of the largest CC at least ``min_area`` big."""
    num, _, stats, _ = cv2.connectedComponentsWithStats(band, connectivity=8)
    if num <= 1:
        return None
    best_i = -1
    best_area = 0
    for i in range(1, num):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        if area > best_area:
            best_area = area
            best_i = i
    if best_i < 0:
        return None
    return int(stats[best_i, cv2.CC_STAT_LEFT]), int(stats[best_i, cv2.CC_STAT_WIDTH])


def _find_active_blob(strip_hsv: np.ndarray) -> tuple[int, int] | None:
    """Return ``(x, width)`` of the active tab capsule, or None.

    Two-pass: first try the raw white mask. If the largest CC's width is
    suspiciously small (<:data:`MIN_ACTIVE_WIDTH_RATIO` of strip), re-run with
    a narrow horizontal closing to bridge intra-capsule gaps where the centre
    icon punches through the middle Y band.
    """
    sh, sw = strip_hsv.shape[:2]
    s_plane = strip_hsv[..., 1]
    v_plane = strip_hsv[..., 2]
    white = (s_plane < ACTIVE_WHITE_MAX_SAT) & (v_plane > ACTIVE_WHITE_MIN_VAL)
    yb0 = int(sh * ACTIVE_BAND_Y_LO)
    yb1 = int(sh * ACTIVE_BAND_Y_HI)
    if yb1 <= yb0:
        return None
    band = (white[yb0:yb1, :].astype(np.uint8)) * 255
    min_area = float(sw * sh) * MIN_ACTIVE_AREA_RATIO

    first = _largest_white_blob(band, min_area)
    if first is not None and first[1] >= sw * MIN_ACTIVE_WIDTH_RATIO:
        return first

    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (ACTIVE_CLOSE_KERNEL_W, 3)
    )
    closed = cv2.morphologyEx(band, cv2.MORPH_CLOSE, kernel)
    return _largest_white_blob(closed, min_area)


def _column_content_ratio(strip_hsv: np.ndarray) -> np.ndarray:
    """Per-column fraction of "tab-content" pixels (bright OR saturated)."""
    sh = strip_hsv.shape[0]
    s_plane = strip_hsv[..., 1]
    v_plane = strip_hsv[..., 2]
    sig = ((v_plane > 90) | (s_plane > 60)).astype(np.uint8)
    upper = sig[: int(sh * 0.7), :]
    return upper.mean(axis=0)


def _walk_slots(
    active_cx: int,
    pitch: int,
    strip_w: int,
    col_ratio: np.ndarray,
) -> list[tuple[int, int, int]]:
    """Walk left/right from the active center, return ``(cx, x0, x1)`` slots.

    The active slot is included as the first element; outward walking stops
    when a slot's mean column-content ratio drops below
    :data:`CONTENT_MIN_COL_RATIO` or the visible width shrinks below a
    quarter of the pitch.
    """
    half = pitch // 2
    min_visible = max(pitch // 4, 5)

    def _clip(cx: int) -> tuple[int, int] | None:
        x0 = max(0, cx - half)
        x1 = min(strip_w, cx + half)
        if x1 - x0 < min_visible:
            return None
        if float(col_ratio[x0:x1].mean()) < CONTENT_MIN_COL_RATIO:
            return None
        return x0, x1

    slots: list[tuple[int, int, int]] = []
    a0 = max(0, active_cx - half)
    a1 = min(strip_w, active_cx + half)
    slots.append((active_cx, a0, a1))

    s = -1
    while True:
        cx = active_cx + s * pitch
        clipped = _clip(cx)
        if clipped is None:
            break
        slots.append((cx, clipped[0], clipped[1]))
        s -= 1

    s = 1
    while True:
        cx = active_cx + s * pitch
        clipped = _clip(cx)
        if clipped is None:
            break
        slots.append((cx, clipped[0], clipped[1]))
        s += 1

    return slots


def _blue_tab_runs(strip_hsv: np.ndarray) -> list[tuple[int, int]]:
    """Return visible blue tab-body runs from the strip top band."""
    sh, sw = strip_hsv.shape[:2]
    y1 = max(1, int(sh * FALLBACK_BAND_Y_HI))
    band = strip_hsv[:y1, :]
    h_plane = band[..., 0]
    s_plane = band[..., 1]
    v_plane = band[..., 2]
    blue = (
        (h_plane > FALLBACK_BLUE_HUE_LO)
        & (h_plane < FALLBACK_BLUE_HUE_HI)
        & (s_plane > FALLBACK_BLUE_MIN_SAT)
        & (v_plane > FALLBACK_BLUE_MIN_VAL)
    )
    col = blue.mean(axis=0)
    kernel_w = max(1, min(FALLBACK_SMOOTH_KERNEL_W, sw))
    kernel = np.ones(kernel_w, dtype=np.float32) / float(kernel_w)
    smoothed = np.convolve(col, kernel, mode="same")
    mask = smoothed > FALLBACK_COL_RATIO

    min_width = max(20, int(round(sw * 0.03)))
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, value in enumerate(mask):
        if bool(value) and start is None:
            start = i
        if (not bool(value) or i == len(mask) - 1) and start is not None:
            end = i if not bool(value) else i + 1
            if end - start >= min_width:
                runs.append((start, end))
            start = None
    return runs


def _tabs_from_runs(
    *,
    image_bgr: np.ndarray,
    patch: np.ndarray,
    px: int,
    py: int,
    strip_h: int,
    runs: list[tuple[int, int]],
) -> list[TabDetection]:
    """Build tab detections from explicit run boundaries."""
    img_h, img_w = image_bgr.shape[:2]
    strip_dots = find_red_dots(patch, image_h_for_norm=img_h)
    dot_xs = [float(d.cx) for d in strip_dots]

    out: list[TabDetection] = []
    for idx, (x0, x1) in enumerate(runs):
        abs_x0 = px + x0
        abs_w = x1 - x0
        bbox_pct = {
            "x": (abs_x0 / img_w) * 100.0,
            "y": (py / img_h) * 100.0,
            "width": (abs_w / img_w) * 100.0,
            "height": (strip_h / img_h) * 100.0,
        }
        dot_pad = max(8.0, float(abs_w) * 0.08)
        has_dot = any((x0 - dot_pad) <= dx < (x1 + dot_pad) for dx in dot_xs)
        active = is_tab_active_in_bbox_percent(image_bgr, bbox_pct)
        out.append(
            TabDetection(
                index=idx,
                bbox_percent=bbox_pct,
                active=active,
                has_red_dot=has_dot,
                color_state=_tab_color_state(image_bgr, bbox_pct, active=active),
                segment_source="blue_runs",
            )
        )
    return out


def _tab_blue_ratio(image_bgr: np.ndarray, bbox_pct: dict[str, float]) -> float:
    patch, _ = patch_bgr_from_bbox_percent(image_bgr, bbox_pct)
    if patch.size == 0:
        return 0.0
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    h_plane = hsv[..., 0]
    s_plane = hsv[..., 1]
    v_plane = hsv[..., 2]
    blue = (
        (h_plane > FALLBACK_BLUE_HUE_LO)
        & (h_plane < FALLBACK_BLUE_HUE_HI)
        & (s_plane > FALLBACK_BLUE_MIN_SAT)
        & (v_plane > FALLBACK_BLUE_MIN_VAL)
    )
    return float(np.count_nonzero(blue)) / float(blue.size)


def _tab_color_state(
    image_bgr: np.ndarray,
    bbox_pct: dict[str, float],
    *,
    active: bool,
) -> str:
    if active:
        return "active_light"
    if _tab_blue_ratio(image_bgr, bbox_pct) >= 0.05:
        return "inactive_blue"
    return "inactive_unknown"


def detect_tabs_in_strip(
    image_bgr: np.ndarray,
    strip_bbox_percent: dict[str, float],
) -> list[TabDetection]:
    """Segment the strip at ``strip_bbox_percent`` and classify each tab.

    Returns an empty list if the active tab cannot be anchored (no white
    capsule, frame too small, etc.). The caller treats that as "no tabs
    detected" — typically a non-tab screen.
    """
    if image_bgr is None or image_bgr.ndim != 3 or image_bgr.size == 0:
        return []
    if not isinstance(strip_bbox_percent, dict):
        return []
    if not all(k in strip_bbox_percent for k in ("x", "y", "width", "height")):
        return []

    patch, (px, py) = patch_bgr_from_bbox_percent(image_bgr, strip_bbox_percent)
    if patch.size == 0:
        return []
    sh, sw = patch.shape[:2]

    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    active = _find_active_blob(hsv)
    if active is None or active[1] < sw * MIN_ACTIVE_WIDTH_RATIO:
        runs = _blue_tab_runs(hsv)
        if runs:
            return _tabs_from_runs(
                image_bgr=image_bgr,
                patch=patch,
                px=px,
                py=py,
                strip_h=sh,
                runs=runs,
            )
        return []
    active_x, active_w = active
    active_cx = active_x + active_w // 2
    pitch = max(int(round(active_w * PITCH_SCALE)), 20)

    img_h, img_w = image_bgr.shape[:2]

    # Validate the anchor: the largest white CC must actually pass the
    # standard tab-active check (low mean S, high mean V over its bbox).
    # White avatar portraits, snow patches, and resource-bar text can all
    # produce a white CC on non-tab screens; their bbox HSV stats fail this
    # gate so we bail rather than inventing a phantom tab grid.
    anchor_bbox_pct = {
        "x": ((px + active_x) / img_w) * 100.0,
        "y": (py / img_h) * 100.0,
        "width": (active_w / img_w) * 100.0,
        "height": (sh / img_h) * 100.0,
    }
    if not is_tab_active_in_bbox_percent(image_bgr, anchor_bbox_pct):
        return []

    col_ratio = _column_content_ratio(hsv)
    slots = _walk_slots(active_cx, pitch, sw, col_ratio)
    slots.sort(key=lambda t: t[0])

    # One pass over the whole strip — a red dot on the active (white) capsule
    # fails the per-tab surround-saturation gate because the immediate
    # neighborhood is white. Detecting against the full strip preserves the
    # surrounding saturated tab-blue context for the gate to pass.
    strip_dots = find_red_dots(patch, image_h_for_norm=img_h)
    dot_xs = [float(d.cx) for d in strip_dots]

    out: list[TabDetection] = []
    for idx, (_, x0, x1) in enumerate(slots):
        abs_x0 = px + x0
        abs_w = x1 - x0
        bbox_pct = {
            "x": (abs_x0 / img_w) * 100.0,
            "y": (py / img_h) * 100.0,
            "width": (abs_w / img_w) * 100.0,
            "height": (sh / img_h) * 100.0,
        }
        # The anchored slot is the active one by construction; every other
        # slot is inactive. Avoid re-running the threshold detector here —
        # white text labels on inactive tabs (e.g. "Training") inflate mean V
        # enough to pass the gate and would mark multiple tabs active.
        is_active = (x0 <= active_cx < x1)
        has_dot = any(x0 <= dx < x1 for dx in dot_xs)

        out.append(
            TabDetection(
                index=idx,
                bbox_percent=bbox_pct,
                active=is_active,
                has_red_dot=has_dot,
                color_state=_tab_color_state(image_bgr, bbox_pct, active=is_active),
                segment_source="active_anchor",
            )
        )

    return out
