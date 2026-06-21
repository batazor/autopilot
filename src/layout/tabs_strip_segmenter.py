"""Programmatic tab-strip segmenter.

Some game screens (shop, backpack, …) render a horizontal strip of tabs where
the selected tab is drawn as a light cream / white capsule and the others as
a saturated mid-blue. The X position of every tab shifts with the active one
because the selected capsule is slightly wider — so per-tab static bboxes in
``area.yaml`` give wrong red-dot results as soon as the user changes tab.

This detector segments the strip dynamically:

1. Find the active tab capsule: the largest cream/white connected component in
   the strip's body Y band (after a small horizontal close that bridges centre
   icons and label text). Trust it as the grid anchor only if it is a wide pill
   (:data:`MIN_ACTIVE_ASPECT`) that also passes the standard tab-active check —
   stray white text/icons come back roughly square and are rejected.
2. Anchor present → use its width as the tab pitch and walk left/right from its
   centre, dropping slots whose column-content ratio collapses (off-screen or
   end of strip). A slot is active iff it contains the anchor centre.
3. Anchor absent (shop product pages whose active item lives in the content
   panel, not the strip; or yellow Trials tabs) → fall back to color-agnostic
   capsule runs: segment every visible blue/cream/yellow tab body and classify
   ``active`` per run via :func:`is_tab_active_in_bbox_percent`.
4. ``has_red_dot`` is set per tab from :func:`find_red_dots`; tabs are returned
   sorted left-to-right with ``index`` and bbox in percent.

Both paths handle uniform pill strips (mail/deals, 5 equal tabs) and the
icon-heavy shop carousel (3-4 variable tabs, partial tabs at the edges).
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

MIN_ACTIVE_ASPECT = 1.5
"""The active capsule is a wide pill: its (closed) width must be at least this
multiple of the anchor band height to be trusted as the grid anchor.

Across shop + mail references a real cream/white capsule measures 2.5-4.5× the
band height, while stray white noise — inactive-tab label text, the white
"30/7" card icon sitting on a blue tab — comes back roughly square (~0.8×) and
is rejected, routing to the color-agnostic capsule-run fallback instead of
shattering the strip into skinny phantom slots.

This aspect gate replaces a former fixed ``%-of-strip-width`` threshold, which
wrongly rejected the legitimately narrow active tab on 5-tab strips (the mail
``System`` capsule is only ~18 % of the strip width yet a perfectly valid
anchor)."""

MAX_ACTIVE_WIDTH_RATIO = 0.70
"""Sanity ceiling: one tab can't span more than this fraction of the strip. A
wider white CC means the anchor latched onto a full-width white panel rather
than a tab — fall back to capsule runs."""

ACTIVE_CLOSE_KERNEL_W = 5
"""Horizontal closing kernel width (px) used to bridge intra-capsule gaps where
a centre icon (shop chest, gem bag) or dark label text punches through the body
Y band. Kept small so it never glues the capsule to a neighbouring white badge
(e.g. the round "!" badge a few px outside the capsule on some shop pages — a
wider kernel would merge them)."""

ACTIVE_FRAGMENT_GROWTH = 1.4
"""If horizontal closing grows the raw white blob by at least this factor, the
raw blob was an icon-fragmented capsule → trust the closed width; otherwise the
raw blob is already an intact pill and closing would only over-merge it with a
neighbouring badge and drift the pitch, so keep the raw width. Calibrated on
shop refs: ``daily_deals`` fragments 111→192 px (×1.73) when the chest punches
the body band, while ``weekly_monthly_cards`` is intact at 191 px and closing
inflates it to 226 px (×1.18)."""

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
"""Color-agnostic capsule-run fallback for strips with no reliable active anchor.

Some shop product pages represent the active item in the content panel rather
than as a white tab in the strip; the active-blob anchor would otherwise latch
onto a tiny white scrap and produce many skinny slots. In that case segment
every visible tab body — blue *or* cream *or* yellow — in the strip's top band.
Unlike the earlier blue-only pass, this never drops a cream/yellow active tab
(which broke 5-tab strips like mail); each run's active state is recovered
per-tab by :func:`is_tab_active_in_bbox_percent` in :func:`_tabs_from_runs`.
"""

CAPSULE_YELLOW_HUE_LO = 15
CAPSULE_YELLOW_HUE_HI = 40
CAPSULE_YELLOW_MIN_SAT = 80
CAPSULE_YELLOW_MIN_VAL = 120
"""Yellow active-tab capsule gate (Trials-style) for the run fallback, mirroring
:func:`layout.tab_active_detector.yellow_tab_ratio`."""

TAB_BODY_ROW_MIN_RATIO = 0.4
"""A strip row belongs to a tab capsule if at least this fraction of the tab's
width is capsule fill (blue/cream/yellow). Each tab's vertical bbox is the
largest contiguous run of such rows, so the click target hugs the real capsule
instead of the padded strip region — taps no longer drift into the gap above or
below the tabs. Measured per tab because the active capsule is taller than the
inactive ones and pops above the row."""


@dataclass(frozen=True)
class TabDetection:
    """One detected tab inside the strip.

    ``index`` is 0-based left-to-right. ``bbox_percent`` is relative to the
    full screen (same convention as area.yaml regions) and spans the full strip
    height — template identification and active/colour detection are calibrated
    against this region. ``tap_bbox_percent`` is the same tab narrowed to the
    real capsule rows (see :func:`_tab_vertical_band`); clicks use it so taps
    land on the tab body instead of the padding above/below the strip. ``active``
    reflects the standard tab-active detector (handles both light and yellow
    variants); ``has_red_dot`` is True iff any classic red badge sits in the
    tab's upper-right quadrant.
    """

    index: int
    bbox_percent: dict[str, float]
    active: bool
    has_red_dot: bool
    color_state: str = "unknown"
    segment_source: str = "unknown"
    tap_bbox_percent: dict[str, float] | None = None


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


def _find_active_blob(strip_hsv: np.ndarray) -> tuple[int, int, int] | None:
    """Return ``(x, width, band_height)`` of the active tab capsule, or None.

    The cream/white mask over the body Y-band is taken both raw and after a
    small horizontal close (:data:`ACTIVE_CLOSE_KERNEL_W`). The raw largest CC
    gives the most accurate width for an intact capsule; the closed CC bridges
    the hole a centre icon / dark label punches through. Keep the raw width
    unless closing grows it by :data:`ACTIVE_FRAGMENT_GROWTH` (raw was
    fragmented) — that avoids over-merging an already-intact pill into a
    neighbour and drifting the pitch. ``band_height`` lets the caller apply the
    :data:`MIN_ACTIVE_ASPECT` pill check; deciding whether to trust the blob as
    an anchor is the caller's job (it also runs the standard tab-active test).
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
    band_h = yb1 - yb0
    min_area = float(sw * sh) * MIN_ACTIVE_AREA_RATIO

    raw = _largest_white_blob(band, min_area)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (ACTIVE_CLOSE_KERNEL_W, 3))
    closed = _largest_white_blob(
        cv2.morphologyEx(band, cv2.MORPH_CLOSE, kernel), min_area
    )

    # Prefer the raw (intact) capsule width; fall back to the closed width only
    # when the raw blob was icon-fragmented (closing grows it markedly) or was
    # never found at all.
    chosen = raw
    if raw is None or (
        closed is not None and closed[1] >= raw[1] * ACTIVE_FRAGMENT_GROWTH
    ):
        chosen = closed
    if chosen is None:
        return None
    return chosen[0], chosen[1], band_h


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


def _capsule_runs(strip_hsv: np.ndarray) -> list[tuple[int, int]]:
    """Return visible tab-body runs, color-agnostic (blue OR cream OR yellow).

    Used when no single active capsule can anchor the grid. Segments every tab
    body in the strip's top band regardless of active/inactive colour, so a
    cream/yellow active tab is never silently dropped (the old blue-only pass
    lost the cream active capsule on 5-tab strips like mail). Each run is
    classified active/red-dot individually by :func:`_tabs_from_runs`.
    """
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
    white = (s_plane < ACTIVE_WHITE_MAX_SAT) & (v_plane > ACTIVE_WHITE_MIN_VAL)
    yellow = (
        (h_plane >= CAPSULE_YELLOW_HUE_LO)
        & (h_plane <= CAPSULE_YELLOW_HUE_HI)
        & (s_plane >= CAPSULE_YELLOW_MIN_SAT)
        & (v_plane >= CAPSULE_YELLOW_MIN_VAL)
    )
    col = (blue | white | yellow).mean(axis=0)
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


def _strip_body_mask(strip_hsv: np.ndarray) -> np.ndarray:
    """Full-height bool mask of capsule-fill pixels (blue OR cream OR yellow)."""
    h_plane = strip_hsv[..., 0]
    s_plane = strip_hsv[..., 1]
    v_plane = strip_hsv[..., 2]
    blue = (
        (h_plane > FALLBACK_BLUE_HUE_LO)
        & (h_plane < FALLBACK_BLUE_HUE_HI)
        & (s_plane > FALLBACK_BLUE_MIN_SAT)
        & (v_plane > FALLBACK_BLUE_MIN_VAL)
    )
    white = (s_plane < ACTIVE_WHITE_MAX_SAT) & (v_plane > ACTIVE_WHITE_MIN_VAL)
    yellow = (
        (h_plane >= CAPSULE_YELLOW_HUE_LO)
        & (h_plane <= CAPSULE_YELLOW_HUE_HI)
        & (s_plane >= CAPSULE_YELLOW_MIN_SAT)
        & (v_plane >= CAPSULE_YELLOW_MIN_VAL)
    )
    return blue | white | yellow


def _tab_vertical_band(
    body_mask: np.ndarray, x0: int, x1: int, strip_h: int
) -> tuple[int, int]:
    """Return the tab's tight ``(y0, y1)`` vertical extent inside its x-range.

    The band is the largest contiguous run of rows whose capsule-fill ratio
    clears :data:`TAB_BODY_ROW_MIN_RATIO` — robust against a red-dot badge or
    stray highlight a few rows above the capsule. Falls back to the full strip
    height when nothing stands out (degenerate / non-tab region).
    """
    sw = body_mask.shape[1]
    lo = max(0, min(int(x0), sw - 1))
    hi = max(lo + 1, min(int(x1), sw))
    rows = body_mask[:, lo:hi].mean(axis=1)
    best_start, best_end, best_len = 0, strip_h, 0
    start: int | None = None
    for i, dense in enumerate(rows >= TAB_BODY_ROW_MIN_RATIO):
        if dense and start is None:
            start = i
        if (not dense or i == len(rows) - 1) and start is not None:
            end = i if not dense else i + 1
            if end - start > best_len:
                best_start, best_end, best_len = start, end, end - start
            start = None
    if best_len == 0:
        return 0, strip_h
    return best_start, best_end


def _full_tab_bbox_pct(
    *, px: int, py: int, x0: int, x1: int, strip_h: int, img_w: int, img_h: int
) -> dict[str, float]:
    """Tab bbox spanning the full strip height — the region template-ID and
    active/colour detection are calibrated against."""
    return {
        "x": ((px + x0) / img_w) * 100.0,
        "y": (py / img_h) * 100.0,
        "width": ((x1 - x0) / img_w) * 100.0,
        "height": (strip_h / img_h) * 100.0,
    }


def _tab_tap_bbox_pct(
    *,
    px: int,
    py: int,
    x0: int,
    x1: int,
    body_mask: np.ndarray,
    strip_h: int,
    img_w: int,
    img_h: int,
) -> dict[str, float]:
    """Tab bbox with the height tightened to the capsule rows (see
    :func:`_tab_vertical_band`) so taps land on the tab body, not the padding."""
    y0, y1 = _tab_vertical_band(body_mask, x0, x1, strip_h)
    return {
        "x": ((px + x0) / img_w) * 100.0,
        "y": ((py + y0) / img_h) * 100.0,
        "width": ((x1 - x0) / img_w) * 100.0,
        "height": ((y1 - y0) / img_h) * 100.0,
    }


def _tabs_from_runs(
    *,
    image_bgr: np.ndarray,
    patch: np.ndarray,
    body_mask: np.ndarray,
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
        bbox_pct = _full_tab_bbox_pct(
            px=px, py=py, x0=x0, x1=x1, strip_h=strip_h, img_w=img_w, img_h=img_h
        )
        tap_bbox_pct = _tab_tap_bbox_pct(
            px=px,
            py=py,
            x0=x0,
            x1=x1,
            body_mask=body_mask,
            strip_h=strip_h,
            img_w=img_w,
            img_h=img_h,
        )
        dot_pad = max(8.0, float(x1 - x0) * 0.08)
        has_dot = any((x0 - dot_pad) <= dx < (x1 + dot_pad) for dx in dot_xs)
        active = is_tab_active_in_bbox_percent(image_bgr, bbox_pct)
        out.append(
            TabDetection(
                index=idx,
                bbox_percent=bbox_pct,
                active=active,
                has_red_dot=has_dot,
                color_state=_tab_color_state(image_bgr, bbox_pct, active=active),
                segment_source="capsule_runs",
                tap_bbox_percent=tap_bbox_pct,
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

    Returns an empty list when the strip holds no detectable tabs (frame too
    small, non-tab screen, etc.). The caller treats that as "no tabs detected".
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
    img_h, img_w = image_bgr.shape[:2]

    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    body_mask = _strip_body_mask(hsv)

    # 1. Anchor on the active capsule when one is reliably present. "Reliable"
    #    means a wide pill (aspect gate) below the panel ceiling whose bbox
    #    passes the standard tab-active check (low mean S, high mean V) — not a
    #    square scrap of white label text / icon (e.g. the "30/7" card icon on
    #    a blue shop tab) nor a full-width white content panel.
    active = _find_active_blob(hsv)
    anchor: tuple[int, int] | None = None
    if active is not None:
        active_x, active_w, band_h = active
        anchor_bbox_pct = {
            "x": ((px + active_x) / img_w) * 100.0,
            "y": (py / img_h) * 100.0,
            "width": (active_w / img_w) * 100.0,
            "height": (sh / img_h) * 100.0,
        }
        wide_enough = active_w >= band_h * MIN_ACTIVE_ASPECT
        not_panel = active_w <= sw * MAX_ACTIVE_WIDTH_RATIO
        if (
            wide_enough
            and not_panel
            and is_tab_active_in_bbox_percent(image_bgr, anchor_bbox_pct)
        ):
            anchor = (active_x, active_w)

    # 2. No trustworthy active capsule (shop product pages whose active item
    #    lives in the content panel, not the strip; yellow Trials tabs) →
    #    segment every visible capsule body color-agnostically and classify
    #    each one individually.
    if anchor is None:
        runs = _capsule_runs(hsv)
        if runs:
            return _tabs_from_runs(
                image_bgr=image_bgr,
                patch=patch,
                body_mask=body_mask,
                px=px,
                py=py,
                strip_h=sh,
                runs=runs,
            )
        return []

    # 3. Anchor found → its width sets the pitch; walk the uniform grid.
    active_x, active_w = anchor
    active_cx = active_x + active_w // 2
    pitch = max(int(round(active_w * PITCH_SCALE)), 20)

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
        bbox_pct = _full_tab_bbox_pct(
            px=px, py=py, x0=x0, x1=x1, strip_h=sh, img_w=img_w, img_h=img_h
        )
        tap_bbox_pct = _tab_tap_bbox_pct(
            px=px,
            py=py,
            x0=x0,
            x1=x1,
            body_mask=body_mask,
            strip_h=sh,
            img_w=img_w,
            img_h=img_h,
        )
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
                tap_bbox_percent=tap_bbox_pct,
            )
        )

    return out
