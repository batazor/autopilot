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


def border_outside_fraction(
    frame: np.ndarray, crop: dict | None, band_frac: float = 0.5,
) -> float:
    """Share of the crop's lower band that is out-of-bounds (beyond the border).

    Out-of-bounds is the dark region reachable by flood-fill from the frame
    edges — the inter-kingdom gap (a dark road) and the area past the yellow
    line read as one connected dark mass, while interior snow stays bright and
    dark *terrain* (mountains, plots) does not connect to the edge as a large
    mass. A robust "am I across the border" signal that does not depend on
    detecting the thin dashed line: empirically ~0.005 well inside the kingdom,
    ~0.7 along an edge, ~1.0 once the camera sits in the gap between states.
    """
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    sub = frame[y0:y1, x0:x1]
    h, w = sub.shape[:2]
    if h < 2 or w < 2:
        return 0.0
    gray = cv2.cvtColor(sub, cv2.COLOR_BGR2GRAY)
    dark = (gray < 95).astype(np.uint8) * 255
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
    band = outside[int(h * (1.0 - band_frac)) :, :]
    return float(np.mean(band)) if band.size else 0.0


def border_band_y(frame: np.ndarray, crop: dict | None) -> float | None:
    """Vertical position (median y, frame coords) of the visible border yellow.

    Used while the crossing itself is not in view: the measured band position
    sizes the next approach step — and flips it upward when the camera has
    overshot past the corner (the line then sits high in the frame), instead
    of descending blindly deeper into the neighbouring state.
    """
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    mask = yellow_boundary_mask(frame[y0:y1, x0:x1])
    ys, _xs = np.nonzero(mask)
    if len(ys) < BORDER_MIN_PIXELS:
        return None
    return y0 + float(np.median(ys))


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
    when the path is clear. A low percentile (not the bare minimum) keeps a
    stray yellow pixel from faking an early border.
    """
    travel = math.hypot(cam_dx, cam_dy)
    if travel < 1e-6:
        return None
    x0, y0, x1, y1 = _crop_bounds(crop, frame.shape)
    mask = yellow_boundary_mask(frame[y0:y1, x0:x1])
    ys, xs = np.nonzero(mask)
    if len(ys) < CROSS_MIN_PIXELS:
        return None
    cx, cy = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    ux, uy = cam_dx / travel, cam_dy / travel
    along = (xs - cx) * ux + (ys - cy) * uy
    across = np.abs((ys - cy) * ux - (xs - cx) * uy)
    ahead = (along > 0) & (across <= corridor_px)
    if int(np.count_nonzero(ahead)) < CROSS_MIN_PIXELS:
        return None
    return float(np.percentile(along[ahead], 10))


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
