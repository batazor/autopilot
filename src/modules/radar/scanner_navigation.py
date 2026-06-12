"""Camera movement + capture primitives for the scanner.

The low layer the scanner and the origin servo both build on: frame
stabilization, the white-label touch guard, reading the minimap viewport rect,
swipe emission (chunked + label-guarded), swipe auto-calibration, and the
pre-move border-crossing guard. No game-flow logic lives here — :mod:`modules.
radar.scanner_servo` and :mod:`modules.radar.scanner` orchestrate these.
"""

from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from modules.radar.border import border_line_ahead_distance, outside_dark_distance

if TYPE_CHECKING:
    from pathlib import Path

    from modules.radar.config import RadarConfig
    from modules.radar.device import RadarDevice

logger = logging.getLogger(__name__)

def _central_region(frame: np.ndarray, cfg: RadarConfig) -> np.ndarray:
    """Middle third of the configured game-area crop — where map content moves."""
    c = cfg.crop
    x0 = c.x + c.w // 3
    y0 = c.y + c.h // 3
    return frame[y0 : y0 + c.h // 3, x0 : x0 + c.w // 3]



@dataclass
class SwipeCalibration:
    """Learned correction of finger travel from measured ORB offsets.

    The map's px-moved-per-px-swiped gain drifts with device/zoom; the stitch
    measures the true offset of every move anyway, so the ratio of expected to
    measured feeds an EMA per axis and subsequent swipes are scaled by it.
    Components shorter than ``min_component_px`` carry too much noise; sign
    mismatches mean the registration locked elsewhere — both are skipped.

    CRITICAL: ``expected`` must be the DESIRED camera travel (the pre-scale
    target), never the commanded finger travel. The commanded finger already
    includes the current scale, so its ratio to the measured offset is the
    game's raw gain — a constant — and feeding it back multiplies the scale by
    that constant every move: geometric divergence. One scan inflated scale_x
    1.0 → 1.53 this way, stretching a whole row until frames stopped
    overlapping. With the desired-travel ratio the error term shrinks as the
    scale converges, the loop's fixed point being measured == desired.
    """

    scale_x: float = 1.0
    scale_y: float = 1.0
    alpha: float = 0.35
    min_scale: float = 0.6
    max_scale: float = 1.6
    min_component_px: float = 120.0

    def update(self, expected: tuple[float, float], measured: tuple[float, float]) -> None:
        for axis in (0, 1):
            e, m = expected[axis], measured[axis]
            if abs(e) < self.min_component_px or abs(m) < 1e-6 or (e > 0) != (m > 0):
                continue
            ratio = min(2.0, max(0.5, e / m))
            current = self.scale_x if axis == 0 else self.scale_y
            updated = current * (1.0 - self.alpha + self.alpha * ratio)
            updated = min(self.max_scale, max(self.min_scale, updated))
            if axis == 0:
                self.scale_x = updated
            else:
                self.scale_y = updated

    def apply(self, finger_dx: float, finger_dy: float) -> tuple[float, float]:
        return finger_dx * self.scale_x, finger_dy * self.scale_y



# Seeds from past manifests are trusted only inside this band: the true
# device gain correction is a few percent, while values far outside it are
# artifacts (runs recorded under the old divergent update law reached 1.53).
SEED_MIN_SCALE = 0.8
SEED_MAX_SCALE = 1.25


def _load_prior_calibration(out_dir: Path) -> SwipeCalibration | None:
    """Seed swipe scales from the most recent sibling run's manifest.

    The learned px-moved-per-px-swiped correction is a property of the
    device/zoom, not of one run — starting every scan back at 1.0 made the
    first rows systematically over/undershoot until the EMA re-converged.
    The values keep adapting from the seed as usual.
    """
    manifests = sorted(
        out_dir.parent.glob("*/manifest.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for path in manifests:
        try:
            saved = json.loads(path.read_text(encoding="utf-8")).get("swipe_calibration")
            if not isinstance(saved, dict):
                continue
            calib = SwipeCalibration()
            sx, sy = float(saved["scale_x"]), float(saved["scale_y"])
            calib.scale_x = min(SEED_MAX_SCALE, max(SEED_MIN_SCALE, sx))
            calib.scale_y = min(SEED_MAX_SCALE, max(SEED_MIN_SCALE, sy))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        logger.info(
            "radar: swipe calibration seeded from %s (scale_x=%.3f, scale_y=%.3f)",
            path.parent.name, calib.scale_x, calib.scale_y,
        )
        return calib
    return None



def wait_stable(device: RadarDevice, cfg: RadarConfig) -> tuple[np.ndarray, bool]:
    """Capture until two consecutive frames stop differing, or time out.

    Returns ``(last_frame, stable)``; on timeout the frame is still returned
    so the caller can save it with an ``unstable`` flag.
    """
    t = cfg.timings
    deadline = time.monotonic() + t.stabilize_timeout_ms / 1000.0
    prev = device.capture()
    hits = 0
    while time.monotonic() < deadline:
        time.sleep(t.stabilize_interval_ms / 1000.0)
        cur = device.capture()
        diff = float(np.mean(cv2.absdiff(_central_region(prev, cfg), _central_region(cur, cfg))))
        prev = cur
        if diff <= t.stabilize_diff_threshold:
            hits += 1
            if hits >= t.stabilize_consecutive:
                return cur, True
        else:
            hits = 0
    logger.warning("frame did not stabilize within %d ms", t.stabilize_timeout_ms)
    return prev, False


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------



def _patch_is_white(frame: np.ndarray, x: int, y: int, cfg: RadarConfig) -> bool:
    """True when the patch around (x, y) is dominated by near-white label pixels."""
    g = cfg.label_guard
    h, w = frame.shape[:2]
    r = g.sample_radius_px
    patch = frame[max(0, y - r) : min(h, y + r + 1), max(0, x - r) : min(w, x + r + 1)]
    if patch.size == 0:
        return False
    white = np.all(patch >= g.white_threshold, axis=2)
    return float(np.mean(white)) >= g.white_fraction



def _wait_touch_clear(device: RadarDevice, cfg: RadarConfig, x: float, y: float) -> None:
    """Hold off touching (x, y) until a white UI label covering it clears.

    City/marker labels and minimap overlays are near-white and transient;
    touching one selects it instead of panning or teleporting. Poll the live
    screen and only return once the point is clear — or, after the timeout,
    touch anyway (a clear is never guaranteed).
    """
    g = cfg.label_guard
    if not g.enabled:
        return
    xi, yi = int(round(x)), int(round(y))
    deadline = time.monotonic() + g.timeout_ms / 1000.0
    waited = False
    while _patch_is_white(device.capture(), xi, yi, cfg):
        if time.monotonic() >= deadline:
            logger.warning(
                "radar: touch point (%d,%d) still under a white label after %dms — touching anyway",
                xi, yi, g.timeout_ms,
            )
            return
        waited = True
        time.sleep(g.poll_interval_ms / 1000.0)
    if waited:
        logger.info("radar: touch point (%d,%d) cleared of label, proceeding", xi, yi)



@dataclass(frozen=True, slots=True)
class MinimapRect:
    """The white viewport rectangle read off the minimap."""

    cx: float
    cy: float
    w: int
    h: int
    # Touching the minimap bbox edge: the rect drawing is clipped by the
    # widget near the kingdom's bottom, so the position reading is a lie
    # there — only comparable against a reference recorded at the same spot.
    clipped: bool



def _viewport_rect(frame: np.ndarray, cfg: RadarConfig) -> MinimapRect | None:
    """Camera position on the minimap: the white viewport rectangle.

    The white component closest in size to the configured viewport rect wins,
    so white labels overlapping the minimap don't hijack the reading.
    """
    bx, by, bw, bh = cfg.minimap.bbox
    mm = frame[by : by + bh, bx : bx + bw]
    white = cv2.inRange(mm, (230, 230, 230), (255, 255, 255))
    # Event markers drawn over the minimap fragment the pin's ring into
    # pieces; closing reunites them so the centroid doesn't wander.
    white = cv2.morphologyEx(
        white, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)),
    )
    count, _labels, stats, centroids = cv2.connectedComponentsWithStats(white)
    best: MinimapRect | None = None
    best_err: float | None = None
    for i in range(1, count):
        x, y, w, h, area = stats[i]
        if area < 30 or w > cfg.viewport.rect_w * 2 or h > cfg.viewport.rect_h * 2:
            continue
        err = abs(w - cfg.viewport.rect_w) + abs(h - cfg.viewport.rect_h)
        if best_err is None or err < best_err:
            clipped = x <= 0 or y <= 0 or x + w >= bw or y + h >= bh
            best = MinimapRect(
                cx=bx + float(centroids[i][0]),
                cy=by + float(centroids[i][1]),
                w=int(w),
                h=int(h),
                clipped=bool(clipped),
            )
            best_err = err
    return best



def _viewport_rect_center(frame: np.ndarray, cfg: RadarConfig) -> tuple[float, float] | None:
    rect = _viewport_rect(frame, cfg)
    return (rect.cx, rect.cy) if rect is not None else None



def _border_swipe_guard(
    cfg: RadarConfig,
    frame: np.ndarray | None,
    minimap_dx: float,
    minimap_dy: float,
) -> tuple[float, float, dict | None]:
    """Shorten a move that would carry the camera across the yellow border.

    The frame captured at the previous cell is probed for the border line
    along the planned motion path; when the planned travel reaches past it,
    the move is scaled down to stop ``cross_margin_px`` short of the line.
    The positional drift this introduces is harmless — the stitcher measures
    real offsets — and every subsequent move re-checks against a fresh frame,
    so the camera rides along the border without ever crossing it.

    A yellowish pixel ahead is NOT a crossing: golden icons, marker rows, pale
    decor trails and the corner X's own arms all fire a raw-pixel test deep
    inside the kingdom (one such false positive pinned a whole scan). The one
    signal that defines "outside" is the NEUTRAL-dark inter-kingdom gap
    (mountains and sprites are tinted and excluded from its mask): a move is
    a crossing only when that mass lies ON the path. The fitted border line
    merely refines where to stop — short of the line when one is in view,
    else short of the dark edge itself. No dark ahead → no crossing (the
    corner arms while moving inward, decor lines, icon noise) — pass; the
    post-capture outside-fraction abort and the servo's gap back-off remain
    the backstops for anything this misses.
    """
    b = cfg.border
    if not b.block_crossing or frame is None:
        return minimap_dx, minimap_dy, None
    c = cfg.crop
    viewport_w = cfg.stitch_viewport.w if cfg.stitch_viewport is not None else c.w
    viewport_h = cfg.stitch_viewport.h if cfg.stitch_viewport is not None else c.h
    cam_dx = (minimap_dx / cfg.viewport.rect_w) * viewport_w
    cam_dy = (minimap_dy / cfg.viewport.rect_h) * viewport_h
    crop = c.model_dump()
    dark_dist = outside_dark_distance(
        frame, crop, cam_dx, cam_dy, corridor_px=b.cross_corridor_px,
    )
    if dark_dist is None:
        return minimap_dx, minimap_dy, None
    line_dist = border_line_ahead_distance(
        frame, crop, cam_dx, cam_dy, corridor_px=b.cross_corridor_px,
    )
    dist = line_dist if line_dist is not None else dark_dist
    travel = math.hypot(cam_dx, cam_dy)
    allowed = max(0.0, dist - b.cross_margin_px)
    if travel <= allowed:
        return minimap_dx, minimap_dy, None
    scale = allowed / travel
    logger.warning(
        "radar: border %.0f px ahead on the move path (planned travel %.0f px) — "
        "shortening the swipe to %.0f%% to stay inside the kingdom",
        dist, travel, scale * 100,
    )
    return (
        minimap_dx * scale,
        minimap_dy * scale,
        {
            "border_distance_px": round(dist, 1),
            "planned_travel_px": round(travel, 1),
            "travel_scale": round(scale, 3),
        },
    )



def _swipe_relative(
    device: RadarDevice,
    cfg: RadarConfig,
    minimap_dx: float,
    minimap_dy: float,
    calib: SwipeCalibration | None = None,
) -> list[dict[str, int]]:
    if abs(minimap_dx) < 1e-6 and abs(minimap_dy) < 1e-6:
        return []
    c = cfg.crop
    nav = cfg.navigation
    viewport_w = cfg.stitch_viewport.w if cfg.stitch_viewport is not None else c.w
    viewport_h = cfg.stitch_viewport.h if cfg.stitch_viewport is not None else c.h
    # To move the camera right/down on the map, drag the map left/up.
    finger_dx = -(minimap_dx / cfg.viewport.rect_w) * viewport_w * nav.swipe_scale
    finger_dy = -(minimap_dy / cfg.viewport.rect_h) * viewport_h * nav.swipe_scale
    if calib is not None:
        finger_dx, finger_dy = calib.apply(finger_dx, finger_dy)
    return _swipe_fingers(device, cfg, finger_dx, finger_dy)



def _swipe_fingers(
    device: RadarDevice,
    cfg: RadarConfig,
    finger_dx: float,
    finger_dy: float,
) -> list[dict[str, int]]:
    """Drag the map by raw finger travel in screen px (chunked, label-guarded)."""
    if abs(finger_dx) < 1e-6 and abs(finger_dy) < 1e-6:
        return []
    c = cfg.crop
    nav = cfg.navigation
    margin_x = min(nav.swipe_margin_px, max(0, c.w // 2 - 1))
    margin_y = min(nav.swipe_margin_px, max(0, c.h // 2 - 1))
    max_dx = max(1, c.w - margin_x * 2)
    max_dy = max(1, c.h - margin_y * 2)
    chunks = max(1, math.ceil(max(abs(finger_dx) / max_dx, abs(finger_dy) / max_dy)))
    step_x = finger_dx / chunks
    step_y = finger_dy / chunks

    emitted: list[dict[str, int]] = []
    for index in range(chunks):
        if index > 0 and nav.chunk_pause_ms > 0:
            # Two quick touches read as the double-tap-drag ZOOM gesture
            # in-game — keep chunked swipes clearly separated in time.
            time.sleep(nav.chunk_pause_ms / 1000.0)
        x1, y1, x2, y2 = _swipe_points_for_delta(
            c.x,
            c.y,
            c.w,
            c.h,
            margin_x,
            margin_y,
            step_x,
            step_y,
        )
        # Don't touch down on a white label — wait it out so the finger grabs
        # the map and pans, instead of selecting a city/marker under the start.
        _wait_touch_clear(device, cfg, x1, y1)
        device.swipe(x1, y1, x2, y2, nav.swipe_duration_ms)
        emitted.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "ms": nav.swipe_duration_ms})
    return emitted



def _swipe_points_for_delta(
    crop_x: int,
    crop_y: int,
    crop_w: int,
    crop_h: int,
    margin_x: int,
    margin_y: int,
    dx: float,
    dy: float,
) -> tuple[int, int, int, int]:
    x1 = crop_x + margin_x if dx >= 0 else crop_x + crop_w - margin_x
    y1 = crop_y + margin_y if dy >= 0 else crop_y + crop_h - margin_y
    x2 = x1 + dx
    y2 = y1 + dy
    min_x, max_x = crop_x + margin_x, crop_x + crop_w - margin_x
    min_y, max_y = crop_y + margin_y, crop_y + crop_h - margin_y
    return (
        int(round(min(max(x1, min_x), max_x))),
        int(round(min(max(y1, min_y), max_y))),
        int(round(min(max(x2, min_x), max_x))),
        int(round(min(max(y2, min_y), max_y))),
    )
