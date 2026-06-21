"""Kingdom-border detection on raw frames.

The kingdom edge is a pale yellow dashed line. At the map's bottom corner the
two side lines cross in an X — a unique, *visible* landmark the scanner can
position against (the minimap tap-teleport is quantized and untrusted), and an
absolute anchor for the stitched map: the crossing is the game-coordinate corner.
"""

from __future__ import annotations

import math

import cv2
import numpy as np

# Pale yellow dashed line; range a little broad because screenshots can be
# darkened by fog/edge overlays.
YELLOW_HSV_LO = (18, 35, 105)
YELLOW_HSV_HI = (42, 255, 255)
# Opening kernel: wider than the dashed line (a few px) but narrower than the
# gold castle / event-marker blobs, so opening removes the line and leaves only
# the blobs to subtract away.
YELLOW_BLOB_KERNEL = (11, 11)
# Fewer yellow pixels than this is noise, not a border in frame.
BORDER_MIN_PIXELS = 80
# Top-corner test: the crossing must sit in this upper fraction of the crop.
TOP_CROSS_FRAC = 0.35
# Crossing guard: fewer yellow pixels than this inside the look-ahead corridor
# means the path is clear (noise level for a dashed line crossing it).
CROSS_MIN_PIXELS = 30
# Border lines are the isometric diamond edges: roughly ±30..45° on screen.
# Slopes outside this band are labels, roads or noise, not the border.
LINE_SLOPE_MIN = 0.2
LINE_SLOPE_MAX = 1.5
# How far outside the crop the fitted intersection may fall and still count
# (fraction of the crop size) — the corner can sit just past the visible edge.
CROSS_OUTSIDE_FRAC = 0.25


def yellow_boundary_mask(img: np.ndarray) -> np.ndarray:
    """Thin yellow border line only — gold blobs (castle, markers) removed."""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    yellow = cv2.inRange(hsv, np.array(YELLOW_HSV_LO), np.array(YELLOW_HSV_HI))
    # The player's own gold castle (and golden event markers) share this hue
    # but are thick solid blobs, not a thin line. Opening with a kernel wider
    # than the dashed line erases the line and keeps the blobs; subtract those
    # back out so only the thin border survives.
    blobs = cv2.morphologyEx(
        yellow,
        cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, YELLOW_BLOB_KERNEL),
    )
    return cv2.subtract(yellow, blobs)


def _crop_bounds(crop: dict | None, shape: tuple[int, ...]) -> tuple[int, int, int, int]:
    h, w = shape[:2]
    if not isinstance(crop, dict):
        return 0, 0, w, h
    x0 = max(0, int(crop.get("x") or 0))
    y0 = max(0, int(crop.get("y") or 0))
    x1 = min(w, x0 + int(crop.get("w") or w))
    y1 = min(h, y0 + int(crop.get("h") or h))
    if x1 <= x0 or y1 <= y0:
        return 0, 0, w, h
    return x0, y0, x1, y1


def _longest_segments_by_slope_sign(
    mask: np.ndarray,
) -> dict[int, tuple[float, float, float, float] | None]:
    """Longest Hough segment per slope sign (+1 down-right, -1 down-left).

    The dashed line is bridged by dilation + a generous ``maxLineGap`` so it
    reads as one long segment instead of many dashes.
    """
    fat = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    segs = cv2.HoughLinesP(
        fat, 1, np.pi / 180.0, threshold=40, minLineLength=60, maxLineGap=40,
    )
    best: dict[int, tuple[float, float, float, float] | None] = {1: None, -1: None}
    best_len: dict[int, float] = {1: 0.0, -1: 0.0}
    if segs is None:
        return best
    for sx1, sy1, sx2, sy2 in segs[:, 0]:
        dx, dy = float(sx2 - sx1), float(sy2 - sy1)
        if abs(dx) < 1e-6:
            continue
        slope = dy / dx
        if not (LINE_SLOPE_MIN <= abs(slope) <= LINE_SLOPE_MAX):
            continue
        sign = 1 if slope > 0 else -1
        length = dx * dx + dy * dy
        if length > best_len[sign]:
            best[sign] = (float(sx1), float(sy1), float(sx2), float(sy2))
            best_len[sign] = length
    return best


def _line_intersection(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> tuple[float, float] | None:
    x1, y1, x2, y2 = a
    x3, y3, x4, y4 = b
    d1x, d1y = x2 - x1, y2 - y1
    d2x, d2y = x4 - x3, y4 - y3
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-9:
        return None
    t = ((x3 - x1) * d2y - (y3 - y1) * d2x) / den
    return x1 + t * d1x, y1 + t * d1y


def find_border_lines(
    frame: np.ndarray, crop: dict | None,
) -> dict[int, tuple[float, float, float, float] | None]:
    """Longest visible border segment per slope sign, in frame coordinates.

    Key +1 is the down-right line (the kingdom's lower-LEFT edge), -1 the
    down-left one (lower-RIGHT edge). Either may be None. Both bottom edges
    descend toward the bottom corner, so following a single visible line
    downhill leads to the crossing.
    """
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    mask = yellow_boundary_mask(frame[y0:y1, x0:x1])
    if int(np.count_nonzero(mask)) < BORDER_MIN_PIXELS:
        return {1: None, -1: None}
    best = _longest_segments_by_slope_sign(mask)
    return {
        sign: (seg[0] + x0, seg[1] + y0, seg[2] + x0, seg[3] + y0) if seg else None
        for sign, seg in best.items()
    }


def find_border_cross(frame: np.ndarray, crop: dict | None) -> tuple[float, float] | None:
    """The X where the two dashed border lines cross — the kingdom's corner.

    Unlike the lowest-yellow-point apex, this needs BOTH lines in view: one
    down-right and one down-left segment are fitted from the yellow mask and
    intersected. A single side border crossing the frame (the failure that
    used to fake an origin lock) yields one slope sign only → None. Returns
    frame coordinates; the point may sit slightly outside the crop (the lines
    are extended), which is fine for servoing toward it.
    """
    lines = find_border_lines(frame, crop)
    if lines[1] is None or lines[-1] is None:
        return None
    cross = _line_intersection(lines[1], lines[-1])
    if cross is None:
        return None
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    mx, my = (x1 - x0) * CROSS_OUTSIDE_FRAC, (y1 - y0) * CROSS_OUTSIDE_FRAC
    if not (x0 - mx <= cross[0] <= x1 + mx and y0 - my <= cross[1] <= y1 + my):
        return None
    return cross


def _outside_mask(
    frame: np.ndarray, crop: dict | None, spread_max: float = 28.0,
) -> tuple[np.ndarray, tuple[int, int]] | None:
    """Boolean mask of out-of-bounds terrain within the crop + crop origin.

    Out-of-bounds is the dark region reachable by flood-fill from the crop
    edges — the inter-kingdom gap (a dark road) and the area past the yellow
    line read as one connected dark mass, while interior snow stays bright and
    dark *terrain* (mountains, plots) does not connect to the edge as a large
    mass. This is the robust border signal: it does not depend on detecting
    the thin dashed line at all. Returns None on a degenerate crop.

    ``spread_max`` bounds how tinted a dark pixel may be and still count as the
    gap. The default (28) is NEUTRAL-grey only — right for the desaturated
    inter-kingdom road, but it discards a strongly snow-tinted gap (measured
    spread ~33, saturation ~140 on a live screen), which is why the live bottom
    corner read 0 out-of-bounds. The flood-fill-from-edge + min-component gate
    below already rejects tinted in-world sprites, so a high ``spread_max``
    (tint-tolerant) is safe — see :func:`border_darkness_fraction`.
    """
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    sub = frame[y0:y1, x0:x1]
    h, w = sub.shape[:2]
    if h < 2 or w < 2:
        return None
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    # NEUTRAL dark only: the inter-kingdom road is desaturated grey (channel
    # spread ~7), while dark in-world content — mountain shading, beast/icon
    # sprites — is strongly tinted (spread 40+). Without the spread test, edge
    # sprites flood-filled as "outside" and faked the gap in every direction.
    spread = sub.max(axis=2).astype(np.int16) - sub.min(axis=2).astype(np.int16)
    dark = ((gray < 95) & (spread < spread_max)).astype(np.uint8) * 255
    dark = cv2.morphologyEx(
        dark, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
    )
    flood = dark.copy()
    ffmask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    for x in range(w):
        if flood[0, x]:
            cv2.floodFill(flood, ffmask, (x, 0), 128)
        if flood[h - 1, x]:
            cv2.floodFill(flood, ffmask, (x, h - 1), 128)
    for y in range(h):
        if flood[y, 0]:
            cv2.floodFill(flood, ffmask, (0, y), 128)
        if flood[y, w - 1]:
            cv2.floodFill(flood, ffmask, (w - 1, y), 128)
    outside = flood == 128
    # Per-component size gate: a neutral-grey sprite (statue base, shadow)
    # touching the crop edge passes both the darkness and the spread test and
    # flood-fills as "outside" — one such blob in the probe corridor capped a
    # whole row's moves at nothing. The genuine gap is never small: even at
    # first sighting the wedge past the border is a sizeable corner triangle,
    # while edge sprites are a fraction of a percent of the crop.
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        outside.astype(np.uint8),
    )
    min_area = outside.size * GAP_MIN_COMPONENT_FRAC
    for i in range(1, count):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            outside[labels == i] = False
    # Fill enclosed holes: tinted content sitting ON the road (beast/monster
    # sprites, decals) fails the neutral-dark test above and would punch holes
    # in the mass — shrinking the in-gap fraction the servo's back-off relies
    # on. Anything not reachable from the crop edges through non-outside
    # pixels is surrounded by the gap, hence part of it.
    comp = (~outside).astype(np.uint8)
    reach = comp.copy()
    ffmask2 = np.zeros((h + 2, w + 2), dtype=np.uint8)
    for x in range(w):
        if reach[0, x] == 1:
            cv2.floodFill(reach, ffmask2, (x, 0), 2)
        if reach[h - 1, x] == 1:
            cv2.floodFill(reach, ffmask2, (x, h - 1), 2)
    for y in range(h):
        if reach[y, 0] == 1:
            cv2.floodFill(reach, ffmask2, (0, y), 2)
        if reach[y, w - 1] == 1:
            cv2.floodFill(reach, ffmask2, (w - 1, y), 2)
    outside |= (comp == 1) & (reach != 2)
    return outside, (x0, y0)


# Fewer out-of-bounds pixels than this share of the crop is specks, not the gap.
GAP_MIN_AREA_FRAC = 0.005
# A single connected "outside" component below this share of the crop is an
# edge-touching sprite, not the gap (used inside _outside_mask, resolved at
# call time). The real gap wedge at first sighting is well above this.
GAP_MIN_COMPONENT_FRAC = 0.01


def border_outside_fraction(
    frame: np.ndarray, crop: dict | None, band_frac: float = 0.5,
) -> float:
    """Share of the crop's lower band that is out-of-bounds (beyond the border).

    Empirically ~0.005 well inside the kingdom, ~0.7 along an edge, ~1.0 once
    the camera sits in the gap between states.
    """
    res = _outside_mask(frame, crop)
    if res is None:
        return 0.0
    outside, _origin = res
    band = outside[int(outside.shape[0] * (1.0 - band_frac)) :, :]
    return float(np.mean(band)) if band.size else 0.0


def border_darkness_fraction(
    frame: np.ndarray, crop: dict | None, band_frac: float = 0.5,
) -> float:
    """Tint-tolerant out-of-bounds fraction — the live-screen border signal.

    :func:`border_outside_fraction` only counts the NEUTRAL-grey gap (the
    inter-kingdom road). On a live screen the gap past the yellow line is
    strongly snow-tinted, so that signal collapses to ~0 even at a real corner
    while the darkness is obvious. This variant flood-fills dark of ANY tint
    (``spread_max`` lifted), relying on the same edge-connectivity + minimum
    component-size gate to reject in-world dark sprites. Measured: ~0.9 at a
    kingdom corner, ~0.5 along an edge, ~0.02 anywhere in the interior
    (including the alliance/territory diamonds that fool ``find_border_cross``).
    """
    res = _outside_mask(frame, crop, spread_max=255.0)
    if res is None:
        return 0.0
    outside, _origin = res
    band = outside[int(outside.shape[0] * (1.0 - band_frac)) :, :]
    return float(np.mean(band)) if band.size else 0.0


# A true kingdom corner shows the border cross AND backs onto the dark
# inter-state gap. Interior alliance/territory diamonds also cross two yellow
# lines (``find_border_cross`` fires on them), but nothing out-of-bounds sits
# behind them. Gated on the tint-tolerant darkness fraction (the neutral-grey
# one reads 0 at a live corner): measured ~0.9 at the genuine corner, ~0.02 at
# every interior false crossing — so this threshold is wide. Kept conservative
# so a mid-edge frame (~0.5, but it has no crossing anyway) is not relied upon.
KINGDOM_CORNER_MIN_OUTSIDE = 0.40


def find_kingdom_corner(
    frame: np.ndarray,
    crop: dict | None,
    min_outside: float = KINGDOM_CORNER_MIN_OUTSIDE,
) -> tuple[float, float] | None:
    """A border crossing that is a *true* kingdom corner, not an inner diamond.

    ``find_border_cross`` alone fires on the alliance/territory borders that
    pattern the kingdom interior — the reason auto-vertex-detection was shelved
    in favour of operator marking (see ``corners.py``). The real corner is the
    only crossing with the out-of-bounds gap behind it, so additionally require
    a substantial :func:`border_darkness_fraction` (tint-tolerant, so it holds
    on a live screen). Returns the crossing in frame coordinates, or None when
    no gated corner is in view.
    """
    cross = find_border_cross(frame, crop)
    if cross is None:
        return None
    if border_darkness_fraction(frame, crop) < min_outside:
        return None
    return cross


def border_outside_top_y(frame: np.ndarray, crop: dict | None) -> float | None:
    """Top edge (frame y) of the out-of-bounds mass in the crop's lower half.

    The dark gap is a huge, reliable signal next to the 4 px dashed line: when
    the line detector fails (Hough misses, fog, markers on the line), the top
    of the dark mass still tells the servo where the border is. Only pixels
    below the crop's vertical center count — during the bottom-corner approach
    the outside is always below, and ignoring the upper half keeps unrelated
    dark areas from hijacking the steering. The 5th percentile (not the bare
    minimum) shrugs off stray specks above the true edge.
    """
    res = _outside_mask(frame, crop)
    if res is None:
        return None
    outside, (_x0, y0) = res
    h = outside.shape[0]
    lower = outside[h // 2 :, :]
    ys, _xs = np.nonzero(lower)
    if len(ys) < outside.size * GAP_MIN_AREA_FRAC:
        return None
    return y0 + h // 2 + float(np.percentile(ys, 5))


def border_band_y(frame: np.ndarray, crop: dict | None) -> float | None:
    """Vertical position (frame y) of the visible border LINE.

    Used while the crossing itself is not in view: the measured band position
    sizes the next approach step — and flips it upward when the camera has
    overshot past the corner (the line then sits high in the frame), instead
    of descending blindly deeper into the neighbouring state.

    Only a Hough-fitted line counts: golden event/resource icons leave plenty
    of scattered yellow-ish pixels, and a bare median over the raw mask once
    steered the servo for 12 straight steps on pure icon noise. No structured
    segment → no band reading; the caller falls through to the dark-mass or
    blind branches, which have their own protections.
    """
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    mask = yellow_boundary_mask(frame[y0:y1, x0:x1])
    if int(np.count_nonzero(mask)) < BORDER_MIN_PIXELS:
        return None
    segments = _longest_segments_by_slope_sign(mask)
    best = max(
        (seg for seg in segments.values() if seg is not None),
        key=lambda s: (s[2] - s[0]) ** 2 + (s[3] - s[1]) ** 2,
        default=None,
    )
    if best is None:
        return None
    return y0 + (best[1] + best[3]) / 2.0


def _ahead_distance(
    ys: np.ndarray,
    xs: np.ndarray,
    center: tuple[float, float],
    cam_dx: float,
    cam_dy: float,
    corridor_px: float,
) -> float | None:
    """10th-percentile distance of mask pixels ahead of ``center`` along the move.

    Pixels are projected onto the motion direction; only those ahead and within
    ``corridor_px`` of the motion axis count. The low percentile (not the bare
    minimum) keeps a stray pixel from faking an early hit.
    """
    travel = math.hypot(cam_dx, cam_dy)
    if travel < 1e-6 or len(ys) < CROSS_MIN_PIXELS:
        return None
    cx, cy = center
    ux, uy = cam_dx / travel, cam_dy / travel
    along = (xs - cx) * ux + (ys - cy) * uy
    across = np.abs((ys - cy) * ux - (xs - cx) * uy)
    ahead = (along > 0) & (across <= corridor_px)
    if int(np.count_nonzero(ahead)) < CROSS_MIN_PIXELS:
        return None
    return float(np.percentile(along[ahead], 10))


def border_cross_distance(
    frame: np.ndarray,
    crop: dict | None,
    cam_dx: float,
    cam_dy: float,
    corridor_px: float = 80.0,
) -> float | None:
    """Distance (px) from the crop center to the border along the camera move.

    ``(cam_dx, cam_dy)`` is the planned camera travel in screen px. Yellow
    pixels are projected onto the motion direction; only those ahead of the
    center and within ``corridor_px`` of the motion axis count. Returns None
    when the path is clear.
    """
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    mask = yellow_boundary_mask(frame[y0:y1, x0:x1])
    ys, xs = np.nonzero(mask)
    return _ahead_distance(
        ys, xs, ((x1 - x0) / 2.0, (y1 - y0) / 2.0), cam_dx, cam_dy, corridor_px,
    )


def border_line_ahead_distance(
    frame: np.ndarray,
    crop: dict | None,
    cam_dx: float,
    cam_dy: float,
    corridor_px: float = 80.0,
) -> float | None:
    """Distance (px) to a HOUGH-FITTED border line along the camera move.

    Raw yellow pixels in the corridor are unreliable — golden icons, marker
    rows and pale trails fire the distance test deep inside the kingdom (one
    such false positive pinned a whole scan row). Only a structured fitted
    segment (proper border slope) counts here; its sampled points are
    projected like every other directional probe. None when no fitted line
    lies ahead in the corridor.
    """
    travel = math.hypot(cam_dx, cam_dy)
    if travel < 1e-6:
        return None
    lines = find_border_lines(frame, crop)
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    ux, uy = cam_dx / travel, cam_dy / travel
    best: float | None = None
    for seg in lines.values():
        if seg is None:
            continue
        sx1, sy1, sx2, sy2 = seg
        n = max(2, int(math.hypot(sx2 - sx1, sy2 - sy1) // 4))
        for k in range(n + 1):
            px = sx1 + (sx2 - sx1) * k / n
            py = sy1 + (sy2 - sy1) * k / n
            along = (px - cx) * ux + (py - cy) * uy
            across = abs((py - cy) * ux - (px - cx) * uy)
            if along > 0 and across <= corridor_px and (best is None or along < best):
                best = along
    return best


def outside_dark_distance(
    frame: np.ndarray,
    crop: dict | None,
    cam_dx: float,
    cam_dy: float,
    corridor_px: float = 80.0,
) -> float | None:
    """Distance (px) from the crop center to the OUT-OF-BOUNDS mass along the move.

    The yellow line alone cannot tell a crossing from a flank: at the bottom
    corner the X's arms span the whole view, so "yellow ahead" is true for
    every direction — including moves INTO the kingdom. The flood-filled dark
    gap is the side-of-the-border ground truth: a move only crosses out when
    the dark mass itself lies on the path. Returns None when no substantial
    outside mass is ahead in the corridor.
    """
    res = _outside_mask(frame, crop)
    if res is None:
        return None
    outside, _origin = res
    if int(np.count_nonzero(outside)) < outside.size * GAP_MIN_AREA_FRAC:
        return None
    ys, xs = np.nonzero(outside)
    h, w = outside.shape
    return _ahead_distance(ys, xs, (w / 2.0, h / 2.0), cam_dx, cam_dy, corridor_px)


def outside_visible(frame: np.ndarray, crop: dict | None) -> bool:
    """Whether a substantial out-of-bounds (dark gap) mass is visible at all."""
    res = _outside_mask(frame, crop)
    if res is None:
        return False
    outside, _origin = res
    return int(np.count_nonzero(outside)) >= outside.size * GAP_MIN_AREA_FRAC


def top_border_visible(frame: np.ndarray, crop: dict | None) -> bool:
    """True when the *top* corner of the kingdom enters the view.

    The top corner is an inverted V: the two dashed lines cross in the upper
    part of the crop and descend AWAY below the crossing. The bottom corner's
    V also paints yellow high in the frame (its arms leave through the top
    edge on both halves), which used to fire the old both-halves band test
    and end a scan a few frames in — so this requires the actual crossing in
    the top zone AND the line bulk BELOW it.
    """
    lines = find_border_lines(frame, crop)
    if lines[1] is None or lines[-1] is None:
        return False
    cross = _line_intersection(lines[1], lines[-1])
    if cross is None:
        return False
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    w, h = x1 - x0, y1 - y0
    mx = w * CROSS_OUTSIDE_FRAC
    if not (x0 - mx <= cross[0] <= x1 + mx):
        return False
    if not (y0 - h * CROSS_OUTSIDE_FRAC <= cross[1] <= y0 + h * TOP_CROSS_FRAC):
        return False
    seg_mid_y = float(
        np.mean([(seg[1] + seg[3]) / 2.0 for seg in (lines[1], lines[-1])]),
    )
    return seg_mid_y > cross[1]
