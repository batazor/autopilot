"""Hook detection for the Fishing Tournament mini-game.

The mini-game drops a fishing hook (an anchor) into a blue underwater scene; the
operator drags it left/right to catch fish. The detector can't classify this
moving target with a fixed template, so we locate the hook geometrically from
three independent on-screen features (validated on ``references/gameplay.png``,
the mandatory 720x1280 frame):

1. **Blue protection ring** — while a shield is active, a bright cyan ring (a
   near-perfect circle) surrounds the hook. It marks the hook centre AND signals
   "protected": when the shield is spent the ring disappears, so its presence is
   itself a gameplay state. The trap is that the *water background* is the same
   blue hue (H~101, S~241, V~189); the ring is separated not by colour but by
   its **glow** — it is brighter and desaturated (V>=215, S<=212). The icebergs
   at the screen edges glow the same way, so the ring search is anchored to the
   hook column (see the green node) to reject them.

2. **Green node** — a small bright-green glowing dot at the very top of the hook
   (H~49). Nothing else in the scene is green, so this is the most robust anchor;
   it pins the hook's horizontal column even when the ring is gone.

3. **Black fishing line** — a thin dark vertical line running from the rod at the
   top of the screen down to the hook. Found as the darkest (V<70) column near
   the hook; its bottom endpoint coincides with the hook top.

The three are fused into a :class:`HookDetection`. ``ring`` present ⇒
``protected``; ``center`` prefers the ring centre, falling back to the green node
then the line, so the hook is still located after the shield drops.

Pure functions over a BGR frame — no device, no Redis — so they unit-test against
the reference PNG and can be driven from an ``exec.py`` FSM later.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Frames are the emulator's mandatory 720x1280; helpers stay resolution-relative
# where it matters, but the tuned pixel sizes below assume this canvas.
FRAME_W, FRAME_H = 720, 1280

# --- Blue protection ring -------------------------------------------------
# The glowing cyan ring vs. the flat blue water, in OpenCV HSV. Same hue band as
# the water, so the SAT ceiling (water sits at S~241) and VAL floor (water at
# V~189; the glow spikes to 229-255) are what isolate the ring. Calibrated from a
# horizontal scan through the ring on references/gameplay.png.
_RING_LO = (88, 40, 215)
_RING_HI = (120, 212, 255)
_RING_SEARCH_DX = 110      # px: ring search half-width around the hook column
_RING_SEARCH_DOWN = 190    # px: how far below the green node the ring can sit
_RING_MIN_AREA = 150       # px: reject specular flecks / stray glow
_RING_MIN_RADIUS = 18
_RING_MAX_RADIUS = 120

# --- Green node -----------------------------------------------------------
_GREEN_LO = (35, 80, 150)
_GREEN_HI = (70, 255, 255)
_GREEN_MIN_AREA = 12
_GREEN_TOP_FRAC = 0.45     # the node lives in the upper screen; ignore below

# --- Black fishing line ---------------------------------------------------
_LINE_V_MAX = 70           # px counted as "dark" (line/rod) when V below this
_LINE_HALF_W = 30          # px: search band half-width around the hook column
_LINE_MIN_PIXELS = 40      # min dark pixels stacked in the column to accept a line


@dataclass(frozen=True)
class Circle:
    """A detected circle in frame pixels. ``circularity`` is the bounding-box
    aspect ratio (1.0 = perfect circle) — the "ideal circle" the ring should be."""

    x: float
    y: float
    r: float
    circularity: float


@dataclass(frozen=True)
class Line:
    """The fishing line as a vertical segment in frame pixels."""

    x: float
    y_top: float
    y_bottom: float


@dataclass(frozen=True)
class HookDetection:
    """Fused result of the three feature detectors for one frame."""

    green_node: tuple[float, float] | None = None
    ring: Circle | None = None
    line: Line | None = None

    @property
    def protected(self) -> bool:
        """True while the blue shield ring is present around the hook."""
        return self.ring is not None

    @property
    def found(self) -> bool:
        """True if any feature located the hook."""
        return self.ring is not None or self.green_node is not None or self.line is not None

    @property
    def center(self) -> tuple[float, float] | None:
        """Best estimate of the hook centre, most-reliable feature first:
        ring centre → green node → line bottom."""
        if self.ring is not None:
            return (self.ring.x, self.ring.y)
        if self.green_node is not None:
            return self.green_node
        if self.line is not None:
            return (self.line.x, self.line.y_bottom)
        return None


def detect_green_node(hsv: np.ndarray) -> tuple[float, float] | None:
    """Locate the green glow at the top of the hook → (x, y) px, or None.

    Picks the largest green blob in the upper screen (its glow centroid). This is
    the most robust anchor — nothing else underwater is green."""
    h = hsv.shape[0]
    mask = cv2.inRange(hsv, _GREEN_LO, _GREEN_HI)
    mask[int(_GREEN_TOP_FRAC * h):, :] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    best_i, best_a = -1, _GREEN_MIN_AREA
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a > best_a:
            best_i, best_a = i, a
    if best_i < 0:
        return None
    ys, xs = np.where(lbl == best_i)
    return (float(xs.mean()), float(ys.mean()))


def detect_blue_ring(hsv: np.ndarray, anchor_x: float | None = None) -> Circle | None:
    """Locate the blue protection ring → :class:`Circle`, or None when no shield.

    The ring shares the water's hue, so it is isolated by its glow (bright +
    desaturated) and the search is constrained to a window around ``anchor_x``
    (the hook column) to reject the same-coloured icebergs at the screen edges.
    Falls back to a full-width upper-screen search when no anchor is given."""
    h, w = hsv.shape[:2]
    mask = cv2.inRange(hsv, _RING_LO, _RING_HI)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

    roi = np.zeros_like(mask)
    if anchor_x is not None:
        x0, x1 = int(max(0, anchor_x - _RING_SEARCH_DX)), int(min(w, anchor_x + _RING_SEARCH_DX))
        # Ring sits below the green node; cap the vertical reach to its glow span.
        roi[: int(_RING_SEARCH_DOWN + 0.30 * h), x0:x1] = 255
    else:
        # No anchor: still restrict to the upper third where the hook lives, and
        # drop the far-left/right iceberg columns.
        roi[: int(0.45 * h), int(0.10 * w): int(0.90 * w)] = 255
    mask = cv2.bitwise_and(mask, roi)

    n, lbl, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    best_i, best_a = -1, _RING_MIN_AREA
    for i in range(1, n):
        a = int(stats[i, cv2.CC_STAT_AREA])
        if a > best_a:
            best_i, best_a = i, a
    if best_i < 0:
        return None

    ys, xs = np.where(lbl == best_i)
    pts = np.column_stack((xs, ys)).astype(np.float32)
    (cx, cy), r = cv2.minEnclosingCircle(pts)
    if not (_RING_MIN_RADIUS <= r <= _RING_MAX_RADIUS):
        return None
    bw = int(stats[best_i, cv2.CC_STAT_WIDTH])
    bh = int(stats[best_i, cv2.CC_STAT_HEIGHT])
    circ = min(bw, bh) / max(bw, bh, 1)
    return Circle(float(cx), float(cy), float(r), float(circ))


def detect_black_line(hsv: np.ndarray, anchor_x: float | None = None) -> Line | None:
    """Locate the black fishing line → :class:`Line`, or None.

    The line is the darkest column near the hook. ``anchor_x`` narrows the band so
    dark fish elsewhere can't masquerade as the line; without it the whole frame
    is scanned for the darkest column."""
    w = hsv.shape[1]
    dark = (hsv[..., 2] < _LINE_V_MAX).astype(np.uint8)

    if anchor_x is not None:
        bx0, bx1 = int(max(0, anchor_x - _LINE_HALF_W)), int(min(w, anchor_x + _LINE_HALF_W))
    else:
        bx0, bx1 = 0, w
    band = dark[:, bx0:bx1]
    cols = band.sum(axis=0)
    if cols.size == 0:
        return None
    lx = bx0 + int(np.argmax(cols))

    # Vertical span of dark pixels in a thin slice centred on the line column.
    slc = dark[:, max(0, lx - 6): lx + 7]
    rows = np.where(slc.sum(axis=1) > 0)[0]
    if rows.size == 0 or int(cols.max()) < _LINE_MIN_PIXELS:
        return None
    return Line(float(lx), float(rows.min()), float(rows.max()))


def detect_hook(frame_bgr: np.ndarray) -> HookDetection:
    """Run all three feature detectors over a BGR frame and fuse them.

    The green node is detected first; its column anchors the ring and line
    searches (rejecting the same-blue icebergs and stray dark fish). When the
    node is missing, the ring's own glow seeds the anchor for the line."""
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)

    green = detect_green_node(hsv)
    anchor = green[0] if green is not None else None

    ring = detect_blue_ring(hsv, anchor_x=anchor)
    if anchor is None and ring is not None:
        anchor = ring.x

    line = detect_black_line(hsv, anchor_x=anchor)
    return HookDetection(green_node=green, ring=ring, line=line)


def annotate(frame_bgr: np.ndarray, det: HookDetection | None = None) -> np.ndarray:
    """Draw a detection over a copy of the frame for debugging / dashboards."""
    if det is None:
        det = detect_hook(frame_bgr)
    out = frame_bgr.copy()
    if det.line is not None:
        cv2.line(out, (int(det.line.x), int(det.line.y_top)),
                 (int(det.line.x), int(det.line.y_bottom)), (0, 0, 255), 2)
    if det.ring is not None:
        cv2.circle(out, (int(det.ring.x), int(det.ring.y)), int(det.ring.r), (0, 255, 255), 2)
        cv2.drawMarker(out, (int(det.ring.x), int(det.ring.y)), (0, 255, 255),
                       cv2.MARKER_CROSS, 18, 2)
    if det.green_node is not None:
        cv2.drawMarker(out, (int(det.green_node[0]), int(det.green_node[1])),
                       (0, 255, 0), cv2.MARKER_TILTED_CROSS, 18, 2)
    return out


if __name__ == "__main__":  # pragma: no cover — manual debugging helper
    import sys

    src = sys.argv[1] if len(sys.argv) > 1 else \
        "games/wos/events/fishing_tournament/references/gameplay.png"
    dst = sys.argv[2] if len(sys.argv) > 2 else "/tmp/hook_annotated.png"
    frame = cv2.imread(src)
    if frame is None:
        msg = f"could not read {src}"
        raise SystemExit(msg)
    result = detect_hook(frame)
    print(f"green_node={result.green_node}")
    print(f"ring={result.ring}")
    print(f"line={result.line}")
    print(f"protected={result.protected} center={result.center}")
    cv2.imwrite(dst, annotate(frame, result))
    print(f"annotated → {dst}")
