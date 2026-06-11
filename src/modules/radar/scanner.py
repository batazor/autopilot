"""Main scan loop: walk the minimap grid → frames + ``manifest.json``.

Default camera movement is relative swipes between grid cells: minimap
tap-teleports proved imprecise (the game clamps/quantizes the jump), while
swipe drift is harmless — the stitcher measures the real frame offsets from
ORB features afterwards, so navigation only needs to land *near* each cell
with enough overlap. ``navigation.mode: tap`` remains available.
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

from modules.radar.border import (
    border_band_y,
    border_cross_distance,
    border_outside_fraction,
    border_outside_top_y,
    find_border_cross,
    find_border_lines,
    top_border_visible,
)
from modules.radar.config import CornerRefConfig, load_config
from modules.radar.device import RadarDevice, ScanStopped, pick_serial
from modules.radar.geometry import (
    Affine,
    diamond_center,
    extend_grid_below,
    generate_grid,
    limit_grid_centered,
    limit_grid_from_bottom,
    scan_walk_from_bottom,
)

if TYPE_CHECKING:
    from pathlib import Path

    from modules.radar.config import RadarConfig
    from modules.radar.events import RadarEventPublisher
    from modules.radar.geometry import GridPoint

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


class ScanAborted(RuntimeError):
    """Unrecoverable scan failure (stale calibration, lost minimap, …)."""


def build_scan_grid(cfg: RadarConfig) -> list[GridPoint]:
    """Scan route — single source of truth for scanner + API.

    Plain serpentine raster for both modes: every move *between captures* is
    a single grid step, which is what keeps neighbouring frames overlapping
    for ORB registration. The camera starts at the minimap center, and the
    (possibly long) positioning move to the first cell happens *before* the
    first capture, so its accuracy never matters for stitching.
    """
    corners = cfg.minimap.corners.as_geometry()
    grid = generate_grid(
        corners,
        cfg.viewport.rect_w,
        cfg.viewport.rect_h,
        overlap=cfg.overlap,
        edge_margin_px=cfg.edge_margin_px,
    )
    if cfg.grid_limit is not None:
        gl = cfg.grid_limit
        if gl.anchor == "bottom":
            if gl.bottom_overscan_rows:
                grid = extend_grid_below(
                    grid,
                    corners,
                    step_y=cfg.viewport.rect_h * (1.0 - cfg.overlap),
                    rows=gl.bottom_overscan_rows,
                    inset_px=gl.bottom_overscan_inset_px,
                )
            grid = limit_grid_from_bottom(grid, gl.max_frames, gl.bottom_skip_rows)
        else:
            grid = limit_grid_centered(grid, corners, gl.cols, gl.rows)
    return grid


def build_scan_walk(cfg: RadarConfig, grid: list[GridPoint]) -> list[tuple[GridPoint, bool]]:
    """Capture route over the scan cells: ``(point, capture)`` per camera step.

    Serpentine modes capture every cell in order (each move a single grid step
    on a full-width raster). The bottom-anchored wedge tapers at the vertex, so
    a plain serpentine would jump across the gap with no overlap — there it uses
    a DFS walk that backtracks through captured cells, keeping every move a
    single overlapping step.
    """
    gl = cfg.grid_limit
    if gl is not None and gl.anchor == "bottom":
        return scan_walk_from_bottom(grid)
    return [(p, True) for p in grid]


def frame_key(ix: int, iy: int) -> str:
    return f"{ix:02d}_{iy:02d}"


def frame_filename(ix: int, iy: int) -> str:
    return f"frame_{ix:02d}_{iy:02d}.png"


# ---------------------------------------------------------------------------
# Stabilization
# ---------------------------------------------------------------------------


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
            calib.scale_x = min(calib.max_scale, max(calib.min_scale, sx))
            calib.scale_y = min(calib.max_scale, max(calib.min_scale, sy))
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            continue
        logger.info(
            "radar: swipe calibration seeded from %s (scale_x=%.3f, scale_y=%.3f)",
            path.parent.name, calib.scale_x, calib.scale_y,
        )
        return calib
    return None


def _guarded_capture(
    device: RadarDevice,
    cfg: RadarConfig,
    prev_frame: np.ndarray | None,
    expected: tuple[float, float] | None,
    reject_path: Path | None = None,
) -> tuple[np.ndarray, bool, tuple[float, float] | None]:
    """Capture a stable frame and verify the view is still the same world.

    Every frame after the first must ORB-register against the previous one
    as a pure pan (same zoom, same screen). A mismatch usually means an
    accidental zoom gesture or a UI transition — wait and recapture a few
    times (covers LOD/icon fade-in), then abort: a knowingly broken map is
    worse than a stopped scan. Returns the measured offset alongside the
    frame — it feeds swipe auto-calibration.

    The prior-gated match can fail on a perfectly valid frame: near the
    kingdom edge the overlap is thin texture plus a grid of identical sprites,
    and ORB locks onto an aliased offset the navigation prior (correctly)
    rejects. That is a stitch-time problem, not a torn view — so before
    aborting, an UNCONSTRAINED match is tried: if it still fits as a clean pan
    (same scale, no rotation) the world is intact and only the offset is
    ambiguous. The frame is kept and the scan continues, but the untrusted
    offset is withheld from calibration (``measured=None``). Only a frame that
    will not register as a pan even without the prior — a real zoom/popup —
    aborts the scan.
    """
    # Local import: stitch imports MANIFEST_NAME from this module at load
    # time, so the reverse import must stay out of module scope.
    from modules.radar.stitch import frames_consistent

    frame, stable = wait_stable(device, cfg)
    if prev_frame is None:
        return frame, stable, None
    crop = cfg.crop.model_dump()
    t = cfg.timings
    for attempt in range(t.zoom_retry_count + 1):
        measured = frames_consistent(prev_frame, frame, crop, expected)
        if measured is not None:
            return frame, stable, measured
        if attempt < t.zoom_retry_count:
            logger.warning(
                "radar: frame does not register against the previous one "
                "(attempt %d/%d) — waiting %dms and recapturing",
                attempt + 1, t.zoom_retry_count, t.zoom_retry_delay_ms,
            )
            time.sleep(t.zoom_retry_delay_ms / 1000.0)
            frame, stable = wait_stable(device, cfg)
    # Prior-gated match exhausted. If the view still registers as a clean pan
    # without the prior, it is the same world at the same zoom — the offset is
    # just aliased (repeated sprites / thin border texture). Keep the frame and
    # carry on; the stitcher re-derives its position from grid neighbours. The
    # ambiguous offset is withheld from swipe calibration.
    if expected is not None and frames_consistent(prev_frame, frame, crop, None) is not None:
        logger.warning(
            "radar: frame registers as a pan but off the expected swipe offset "
            "(aliasing/thin texture near the border) — keeping it, offset not "
            "trusted for calibration",
        )
        return frame, stable, None
    if reject_path is not None:
        # Keep the evidence: the rejected frame shows WHAT the camera saw
        # (zoom level, popup, transition) when the scan had to stop.
        cv2.imwrite(str(reject_path), frame)
        logger.warning("radar: rejected frame saved to %s", reject_path)
    msg = (
        "zoom or view changed mid-scan (frame no longer registers against "
        "the previous one) — reset the camera to the world map and rescan"
    )
    raise ScanAborted(msg)


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


def _load_manifest(out_dir: Path, cfg: RadarConfig, grid: list[GridPoint]) -> dict:
    path = out_dir / MANIFEST_NAME
    cfg_dump = cfg.model_dump(mode="json")
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("config") != cfg_dump:
            msg = (
                f"{path} was produced with a different radar config — "
                "frame indices would not be comparable; use a fresh --out directory"
            )
            raise ScanAborted(msg)
        return manifest
    return {
        "config": cfg_dump,
        # Cell list is stored (not just the count) so the UI can draw the
        # diamond layout for a finished or in-progress run without recomputing
        # the grid geometry client-side.
        "grid": {
            "count": len(grid),
            "points": [{"ix": p.ix, "iy": p.iy} for p in grid],
        },
        "frames": {},
    }


def _save_manifest(out_dir: Path, manifest: dict) -> None:
    path = out_dir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Scan loop
# ---------------------------------------------------------------------------


def run_scan(
    config_path: Path,
    out_dir: Path,
    *,
    serial: str | None = None,
    adb_bin: str | None = None,
    events: RadarEventPublisher | None = None,
) -> None:
    cfg = load_config(config_path)
    bin_pref = adb_bin or cfg.adb_bin
    device_serial = serial or cfg.device_serial or pick_serial(bin_pref)
    device = RadarDevice(device_serial, bin_pref)
    if events is not None:
        # Stop must land mid-step, not between cells: every blocking loop goes
        # through device capture/tap/swipe, so the device polls the stop flag
        # (lightly cached — one Redis GET per ~250ms, not per screenshot).
        cache = {"t": 0.0, "v": False}

        def _should_stop() -> bool:
            now = time.monotonic()
            if now - cache["t"] > 0.25:
                cache["v"] = events.stop_requested()
                cache["t"] = now
            return cache["v"]

        device.abort_check = _should_stop

    corners = cfg.minimap.corners.as_geometry()
    grid = build_scan_grid(cfg)
    affine = Affine.from_corners(corners, cfg.game_size)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(out_dir, cfg, grid)
    started = time.monotonic()
    if events is not None:
        events.scan_started(len(grid), [(p.ix, p.iy) for p in grid])
    try:
        stopped = _scan_grid(device, cfg, grid, affine, manifest, out_dir, events)
    except ScanStopped:
        logger.info("radar: stop requested — scan interrupted mid-step")
        stopped = True
    except Exception as exc:
        if events is not None:
            events.scan_failed(str(exc))
        raise
    if events is not None:
        events.scan_finished(time.monotonic() - started, stopped=stopped)


def _scan_grid(
    device: RadarDevice,
    cfg: RadarConfig,
    grid: list[GridPoint],
    affine: Affine,
    manifest: dict,
    out_dir: Path,
    events: RadarEventPublisher | None,
) -> bool:
    """Capture the walk. Returns ``True`` if it ended early on a stop request."""
    # Local import — stitch imports MANIFEST_NAME from this module at load
    # time, so the reverse import must stay out of module scope.
    from modules.radar.stitch import move_prior

    frames: dict = manifest["frames"]
    walk = build_scan_walk(cfg, grid)
    calib = None
    if cfg.navigation.swipe_autoscale:
        calib = _load_prior_calibration(out_dir) or SwipeCalibration()
    crop_dict = cfg.crop.model_dump()

    done = 0
    skipped = 0
    unstable = 0
    total = len(grid)
    previous: GridPoint | None = None
    # Row where the kingdom's top corner entered the view: finish that row for
    # full coverage, then end the scan — it is complete, not interrupted.
    top_border_row: int | None = None
    # Frames captured this session, by cell. The walk always steps to a grid
    # neighbour, so a new frame registers against the cell it arrived from —
    # which holds even when the route backtracks through earlier cells.
    captured_frames: dict[tuple[int, int], np.ndarray] = {}
    for point, capture in walk:
        # Stop is cooperative: finish nothing further, leave the frames so far
        # for the stitcher. Checked per step so it lands within one frame.
        if events is not None and events.stop_requested():
            logger.info("radar: stop requested — ending scan after %d frame(s)", done)
            return True
        if top_border_row is not None and point.iy != top_border_row:
            logger.info(
                "radar: top border reached at row iy=%d — scan complete (%d frames)",
                top_border_row, done + skipped,
            )
            break

        key = frame_key(point.ix, point.iy)
        filename = frame_filename(point.ix, point.iy)
        already = key in frames and (out_dir / filename).is_file()

        if capture and already:
            skipped += 1
            previous = point
            if events is not None:
                # Resumed run: replay already-present frames so the UI's
                # progress diamond prefills instead of starting empty.
                events.frame_done(
                    point.ix,
                    point.iy,
                    unstable=bool(frames[key].get("unstable")),
                    done=done + skipped,
                    total=total,
                )
            continue

        # Register against the frame we just stepped off of (a grid neighbour);
        # None on the first capture or a resumed gap where it isn't in memory.
        # The same frame feeds the border guard: it shows whether the yellow
        # line lies on the upcoming move path.
        ref_frame = captured_frames.get((previous.ix, previous.iy)) if previous else None
        move_meta = _move_to_point(device, cfg, previous, point, calib, ref_frame, out_dir)
        previous = point
        if not capture:
            # Backtrack step: re-walk an already-captured cell to reach an
            # unvisited branch — move the camera, capture nothing.
            continue

        time.sleep(cfg.timings.post_tap_delay_ms / 1000.0)
        expected = move_prior({"move": move_meta})
        frame, stable, measured = _guarded_capture(
            device, cfg, ref_frame, expected,
            reject_path=out_dir / f"rejected_{key}.png",
        )
        if border_outside_fraction(frame, crop_dict) >= cfg.border.gap_back_off_frac:
            # The camera crossed the border mid-walk despite the pre-swipe
            # guards — a scan of the neighbouring state is garbage; stop with
            # the frame as evidence rather than keep capturing.
            evidence = out_dir / f"outside_{key}.png"
            cv2.imwrite(str(evidence), frame)
            msg = (
                f"frame {key} is outside the kingdom (camera crossed the border "
                f"mid-walk) — evidence saved to {evidence.name}"
            )
            raise ScanAborted(msg)
        if calib is not None and expected is not None and measured is not None:
            calib.update(expected, measured)
            manifest["swipe_calibration"] = {
                "scale_x": round(calib.scale_x, 4),
                "scale_y": round(calib.scale_y, 4),
            }
        top_cross: tuple[float, float] | None = None
        if (
            top_border_row is None
            and cfg.border.stop_at_top
            and top_border_visible(frame, crop_dict)
        ):
            top_border_row = point.iy
            # The crossing of the top corner is the second absolute anchor for
            # the stitched map (game (0, 0)) — the bottom V being the first.
            top_cross = find_border_cross(frame, crop_dict)
            logger.info(
                "radar: top border entered the view — finishing row iy=%d (corner %s)",
                point.iy,
                "not fitted" if top_cross is None else f"at ({top_cross[0]:.0f}, {top_cross[1]:.0f})",
            )
        captured_frames[(point.ix, point.iy)] = frame
        if not stable:
            unstable += 1
        manifest.setdefault("frame_size", {"w": int(frame.shape[1]), "h": int(frame.shape[0])})

        # Save the frame as-is (no UI crop): one coordinate system for capture
        # and stitch. The HUD bakes into tiles for now — accepted trade-off
        # while the placement geometry is being tuned.
        if not cv2.imwrite(str(out_dir / filename), frame):
            msg = f"failed to write {out_dir / filename}"
            raise ScanAborted(msg)
        entry = {
            "ix": point.ix,
            "iy": point.iy,
            "tap_px": [point.x, point.y],
            "move": move_meta,
            "planned_game_xy": [round(v, 2) for v in affine.to_game((point.x, point.y))],
            "file": filename,
            "unstable": not stable,
            "ts": time.time(),
        }
        if top_cross is not None:
            entry["top_cross_px"] = [round(top_cross[0], 1), round(top_cross[1], 1)]
        frames[key] = entry
        _save_manifest(out_dir, manifest)
        done += 1
        if events is not None:
            events.frame_done(
                point.ix,
                point.iy,
                unstable=not stable,
                done=done + skipped,
                total=total,
            )
        logger.info("frame %s saved (%d done, %d/%d total)", key, done, done + skipped, total)

    logger.info(
        "scan complete: %d captured, %d already present, %d unstable → %s",
        done,
        skipped,
        unstable,
        out_dir,
    )
    return False


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


# Origin positioning: the minimap tap-teleport is untrusted (the game
# quantizes/redirects it — observed jumping to the right corner), so the
# scanner verifies where the camera actually landed by reading the white
# viewport rect off the minimap and corrects the residual with swipes.
ORIGIN_MAX_CORRECTIONS = 3
ORIGIN_TOLERANCE_PX = 8.0
# Servo trims shorter than this re-measure with a single settled capture
# instead of the full stabilization loop — the view barely changed.
SERVO_FAST_MEASURE_MAX_PX = 120.0
# A fitted line crossing must sit within this of the dark out-of-bounds edge
# (when one is measured) — disagreement means a phantom Hough lock.
CROSS_GAP_AGREE_PX = 200.0
# Minimap rect size may deviate this much from calibration before the zoom is
# declared wrong (the whole px<->tile geometry hangs off the calibrated zoom).
ZOOM_SIZE_TOLERANCE = 0.25
# Movement feedback: a swipe achieving less than this share of its commanded
# travel is being eaten by the game (rubber-band/pan clamp)...
SERVO_MIN_MOVE_EFFECT = 0.3
# ...and this many underperforming downward moves in a row = the descend is
# clamped: stop marching, switch to lateral search for the corner.
SERVO_CLAMP_STRIKES = 2


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


def _slide_toward_corner(
    seg: tuple[float, float, float, float], step_px: float,
) -> tuple[float, float]:
    """Content move (finger px) that slides the camera downhill along *seg*.

    Both bottom edges of the diamond descend toward the bottom corner, so
    downhill along the visible line is always toward the crossing. Motion
    parallel to the line keeps it in frame. Camera moves downhill by
    ``step_px`` → content (finger) moves the opposite way.
    """
    dx, dy = seg[2] - seg[0], seg[3] - seg[1]
    if dy < 0:
        dx, dy = -dx, -dy
    norm = math.hypot(dx, dy)
    return -dx / norm * step_px, -dy / norm * step_px


def _servo_to_border(
    device: RadarDevice, cfg: RadarConfig, debug_dir: Path | None = None,
) -> dict:
    """Close the loop on the border-line crossing: measure, swipe, repeat.

    The X where the two dashed yellow lines cross is the kingdom's bottom
    corner — it is steered on BOTH axes into (crop center, ``target_frac``),
    so the origin is locked laterally too, not just in height (a sideways
    offset here used to walk the whole first column outside the kingdom).
    The approach length is measured, not guessed; per measurement, in order:

    - lower band out-of-bounds (in the inter-kingdom gap) → climb back;
    - crossing in view (and agreeing with the dark edge) → 2D correction;
    - a yellow line in view → band steering / slide along it to the corner;
    - NO yellow but the dark out-of-bounds mass visible below → steer its top
      edge onto the target height. The dashed-line detector failing is exactly
      how the camera skated across before: the dark mass is the signal that
      never fails when the border is actually near;
    - nothing at all → blind ``approach_step_screens`` descend, total blind
      travel capped at ``max_blind_screens``.

    Dragging the finger by ``e`` px moves the content (and the crossing) by
    ``e``, so each correction is simply the remaining error. Exits with a
    *freshly measured* crossing — the last loop action is always a
    measurement, never a swipe, so the reading matches the upcoming frame.
    With ``require_cross`` the scan never starts blind: no crossing after
    ``max_steps`` (or the blind cap) aborts instead of capturing garbage.

    Movement feedback: every swipe's ACHIEVED displacement is measured by
    ORB-registering consecutive servo frames. The game rubber-bands/clamps
    panning near the world edge — without feedback the servo burned its blind
    budget marching in place, convinced it was descending. Two consecutive
    underperforming downward moves = the descend is clamped: stop pushing and
    search laterally for the corner (vertical is clamped, lateral is not),
    guided by the recorded corner reference when one is calibrated.

    Every measurement is appended to a ``servo_trail`` (decision + readings +
    moves + effectiveness) returned in the meta, and non-nominal decisions
    save their frame to ``debug_dir`` — every past crossing was undiagnosable
    because nothing of what the servo saw survived.
    """
    # Local import — stitch imports MANIFEST_NAME from this module at load
    # time, so the reverse import must stay out of module scope.
    from modules.radar.stitch import frames_consistent

    b = cfg.border
    c = cfg.crop
    viewport_h = cfg.stitch_viewport.h if cfg.stitch_viewport is not None else c.h
    viewport_w = cfg.stitch_viewport.w if cfg.stitch_viewport is not None else c.w
    step_px = b.approach_step_screens * viewport_h
    # Cap one slide so it cannot blow past the corner sideways; re-measurement
    # then catches the crossing within a step or two.
    slide_cap_px = min(step_px, float(b.cross_margin_px))
    max_blind_px = b.max_blind_screens * viewport_h
    crop = c.model_dump()
    if cfg.corner_ref is not None:
        # Aim for the recorded, PROVABLY REACHABLE corner view — the operator
        # panned the camera there by hand. The theoretical (center, target_frac)
        # point can sit beyond the pan clamp, leaving the servo chasing a view
        # the game will never grant.
        target = (float(cfg.corner_ref.cross_px[0]), float(cfg.corner_ref.cross_px[1]))
    else:
        target = (c.x + c.w / 2.0, c.y + c.h * b.target_frac)
    cross: tuple[float, float] | None = None
    steps = 0
    blind_px = 0.0
    last_move_px: float | None = None
    trail: list[dict] = []
    prev_frame: np.ndarray | None = None
    # Camera-space move expected from the last swipe (= -finger move).
    commanded: tuple[float, float] | None = None
    clamp_strikes = 0
    descend_clamped = False
    probe_index = 0
    while True:
        if last_move_px is not None and last_move_px <= SERVO_FAST_MEASURE_MAX_PX:
            # A short trim barely disturbs the view — one settled capture is
            # enough; the full stabilization loop would double the servo time.
            time.sleep(cfg.timings.post_tap_delay_ms / 1000.0)
            frame = device.capture()
        else:
            frame, _stable = wait_stable(device, cfg)

        # Movement feedback for the previous swipe: how much of the commanded
        # travel actually happened, measured along the commanded direction.
        effectiveness: float | None = None
        if prev_frame is not None and commanded is not None:
            cmd_norm = math.hypot(*commanded)
            measured = frames_consistent(prev_frame, frame, crop, None)
            if measured is not None and cmd_norm > 1e-6:
                along = (measured[0] * commanded[0] + measured[1] * commanded[1]) / cmd_norm
                effectiveness = along / cmd_norm
                downward = commanded[1] > abs(commanded[0])
                if downward and effectiveness < SERVO_MIN_MOVE_EFFECT:
                    clamp_strikes += 1
                    if clamp_strikes >= SERVO_CLAMP_STRIKES and not descend_clamped:
                        descend_clamped = True
                        logger.warning(
                            "radar: pan clamp detected — downward gestures move the camera "
                            "at %.0f%% of the commanded travel; switching to lateral search",
                            max(0.0, effectiveness) * 100,
                        )
                elif downward and effectiveness > 0.5:
                    clamp_strikes = 0
                    descend_clamped = False
        prev_frame = frame
        commanded = None

        outside_lower = border_outside_fraction(frame, crop)
        gap_top = border_outside_top_y(frame, crop)
        in_gap = outside_lower >= b.gap_back_off_frac
        cross = None if in_gap else find_border_cross(frame, crop)
        if (
            cross is not None
            and gap_top is not None
            and abs(cross[1] - gap_top) > CROSS_GAP_AGREE_PX
        ):
            # The fitted crossing sits far from the measured dark edge — a
            # phantom Hough lock (label trail, marker row). The dark mass is
            # ground truth; distrust the lines this round.
            logger.info(
                "radar: fitted crossing y=%.0f disagrees with the dark edge y=%.0f — ignored",
                cross[1], gap_top,
            )
            cross = None

        err: tuple[float, float] | None = None
        if in_gap:
            # Across the border (robust signal, needs no line): climb straight
            # back; any "crossing" here would be the next state's.
            decision = "gap_climb"
            logger.warning(
                "radar: camera is in the inter-kingdom gap (outside %.2f ≥ %.2f) — "
                "climbing back toward the kingdom",
                outside_lower, b.gap_back_off_frac,
            )
            err = (0.0, step_px)  # finger down → camera up → back inside
        elif cross is not None:
            err = (target[0] - cross[0], target[1] - cross[1])
            if math.hypot(*err) <= b.tolerance_px:
                logger.info(
                    "radar: border lines cross at (%.0f, %.0f) (target %.0f, %.0f) — origin locked",
                    cross[0], cross[1], target[0], target[1],
                )
                trail.append(_servo_trail_entry("lock", outside_lower, gap_top, cross, None))
                break
            decision = "cross"
        else:
            band_y = border_band_y(frame, crop)
            if band_y is not None:
                if abs(target[1] - band_y) > b.tolerance_px:
                    # A line is visible but vertically off — measured vertical
                    # correction (upward after an overshoot past the corner).
                    decision = "band"
                    err = (0.0, target[1] - band_y)
                else:
                    # Line at the right height, crossing not in view — the
                    # corner is off to the side. Slide downhill along the line.
                    lines = find_border_lines(frame, crop)
                    seg = lines[1] or lines[-1]
                    if seg is None:
                        decision = "band"
                        err = (0.0, target[1] - band_y)
                    else:
                        decision = "slide"
                        slide = _slide_toward_corner(seg, slide_cap_px)
                        logger.info(
                            "radar: line in view but no crossing — sliding %.0f px toward the corner",
                            slide_cap_px,
                        )
                        err = (slide[0], slide[1] + (target[1] - band_y))
            elif gap_top is not None:
                # The dashed line is undetected but the dark mass below is not
                # arguable — steer its top edge onto the target height. This
                # also CLIMBS when the edge is above the target (half-crossed).
                decision = "dark_steer"
                err = (0.0, target[1] - gap_top)
                logger.info(
                    "radar: line undetected, steering on the dark border edge at y=%.0f",
                    gap_top,
                )
            else:
                decision = "blind"
                err = None

        if descend_clamped and decision in ("blind", "dark_steer") and (err is None or err[1] < 0):
            # The descend is clamped by the game — pushing further down is
            # marching in place. The corner must be off to the side: search
            # laterally (vertical is clamped, lateral is not).
            ref = cfg.corner_ref
            cur_rect = _viewport_rect(frame, cfg)
            if ref is not None and ref.rect_px is not None and cur_rect is not None:
                # Align to the recorded corner reading. Only x is meaningful on
                # a clipped rect (y is display-clamped) — and the reference was
                # recorded clipped the same way, so x compares cleanly.
                dx_minimap = ref.rect_px[0] - cur_rect.cx
                if abs(dx_minimap) <= ORIGIN_TOLERANCE_PX:
                    if debug_dir is not None:
                        cv2.imwrite(str(debug_dir / "servo_at_ref_no_cross.png"), frame)
                    msg = (
                        "camera is at the calibrated corner position (minimap rect "
                        f"x={cur_rect.cx:.0f} vs reference {ref.rect_px[0]:.0f}) but the "
                        "dashed-line crossing is not visible — recalibrate the corner "
                        "reference (POST /api/radar/corner-ref) or check the view state"
                    )
                    raise ScanAborted(msg)
                decision = "ref_align"
                cam_dx = (dx_minimap / cfg.viewport.rect_w) * viewport_w
                err = (-cam_dx, 0.0)  # content move = -camera move
                logger.info(
                    "radar: aligning to the corner reference — %.0f minimap px to the %s",
                    abs(dx_minimap), "right" if dx_minimap > 0 else "left",
                )
            else:
                # No reference recorded — expanding zigzag along the clamp:
                # camera offsets +1, -1, +2, -2 … steps from where it stands.
                decision = "lateral_probe"
                k = probe_index
                probe_index += 1
                cam_dx = (k + 1) * step_px * (1 if k % 2 == 0 else -1)
                err = (-cam_dx, 0.0)
                logger.info(
                    "radar: lateral probe %d — moving %.0f px %s along the clamp",
                    k + 1, abs(cam_dx), "right" if cam_dx > 0 else "left",
                )

        trail.append(
            _servo_trail_entry(decision, outside_lower, gap_top, cross, err, effectiveness),
        )
        if debug_dir is not None and decision in (
            "gap_climb", "dark_steer", "blind", "lateral_probe", "ref_align",
        ):
            # Non-nominal sightings are the evidence every past crossing lacked.
            cv2.imwrite(str(debug_dir / f"servo_step{steps:02d}_{decision}.png"), frame)

        # Blind descend that never found the border has crossed the vertex into
        # the next state — stop before marching deeper, same as running out of
        # steps. (decision == "blind" means neither yellow nor dark is visible.)
        blind_exhausted = decision == "blind" and blind_px >= max_blind_px
        if steps >= b.max_steps or blind_exhausted:
            if debug_dir is not None:
                evidence = debug_dir / "servo_giveup.png"
                cv2.imwrite(str(evidence), frame)
                logger.warning("radar: servo give-up frame saved to %s", evidence)
            if cross is None and b.require_cross:
                if descend_clamped:
                    reason = (
                        "the game's pan clamp was reached (gestures verified "
                        "ineffective) and the lateral search did not reveal it"
                    )
                elif blind_exhausted:
                    reason = (
                        f"descended {blind_px / viewport_h:.1f} screen(s) without the "
                        "border ever appearing — the start cell is likely outside the "
                        "kingdom or the camera crossed the vertex"
                    )
                else:
                    reason = f"never entered the view after {steps} servo step(s)"
                msg = (
                    f"kingdom corner (dashed border-line crossing): {reason} — not "
                    "starting a blind scan; calibrate the corner reference "
                    "(POST /api/radar/corner-ref) or check zoom/calibration"
                )
                raise ScanAborted(msg)
            logger.warning(
                "radar: border servo gave up after %d step(s) (crossing %s) — scanning anyway",
                steps,
                "not visible" if cross is None else f"at ({cross[0]:.0f}, {cross[1]:.0f})",
            )
            break
        steps += 1
        if err is None:
            # Nothing visible anywhere — keep descending toward the bottom
            # corner, counting the blind travel against the cap above.
            _swipe_fingers(device, cfg, 0.0, -step_px)
            blind_px += step_px
            last_move_px = step_px
            commanded = (0.0, step_px)  # camera down
        else:
            _swipe_fingers(device, cfg, err[0], err[1])
            last_move_px = math.hypot(*err)
            commanded = (-err[0], -err[1])  # camera move = -content move
    return {
        # Key kept as border_apex_px: the crossing IS the corner the stitcher
        # anchors to game (game_size-1, game_size-1).
        "border_apex_px": [round(cross[0], 1), round(cross[1], 1)] if cross else None,
        "servo_steps": steps,
        "servo_trail": trail,
    }


def _servo_trail_entry(
    decision: str,
    outside_lower: float,
    gap_top: float | None,
    cross: tuple[float, float] | None,
    move: tuple[float, float] | None,
    effectiveness: float | None = None,
) -> dict:
    return {
        "decision": decision,
        "outside_lower": round(outside_lower, 3),
        "gap_top_y": round(gap_top, 1) if gap_top is not None else None,
        "cross": [round(cross[0], 1), round(cross[1], 1)] if cross else None,
        "move_px": [round(move[0], 1), round(move[1], 1)] if move else None,
        "effectiveness": round(effectiveness, 3) if effectiveness is not None else None,
    }


def capture_corner_reference(frame: np.ndarray, cfg: RadarConfig) -> CornerRefConfig:
    """Record the corner reference from a manually positioned screen.

    The operator pans the camera so the bottom-corner X is clearly visible and
    triggers this once (dashboard / ``POST /api/radar/corner-ref``). What the
    servo previously had to guess — where the minimap rect reads at the corner
    (display-clamped!), how dark the lower band is, where the X sits — becomes
    a recorded fact it can verify against and align to.
    """
    crop = cfg.crop.model_dump()
    cross = find_border_cross(frame, crop)
    if cross is None:
        msg = (
            "the dashed-line crossing is not detectable on this screen — pan the "
            "camera so the kingdom corner X is clearly visible, then retry"
        )
        raise ValueError(msg)
    rect = _viewport_rect(frame, cfg)
    return CornerRefConfig(
        cross_px=(round(cross[0], 1), round(cross[1], 1)),
        rect_px=(round(rect.cx, 1), round(rect.cy, 1)) if rect is not None else None,
        rect_size=(rect.w, rect.h) if rect is not None else None,
        outside_lower=round(border_outside_fraction(frame, crop), 3),
    )


def _servo_safe_tap_point(cfg: RadarConfig) -> tuple[float, float]:
    """Origin tap target on the minimap: a small fixed inset above the vertex.

    Inset in PX, not a diamond fraction: the minimap's white "rect" is a
    fixed-size pin, and the true world scale is only ~4 minimap px per screen
    — a fraction-based tap (25% ≈ 17 px) landed 4+ screens above the corner,
    beyond any blind budget. ``safe_tap_inset_px`` (~6 px ≈ 1.5 screens) puts
    the camera safely inside yet within the servo's measured descend.
    """
    corners = cfg.minimap.corners.as_geometry()
    cx, cy = diamond_center(corners)
    bx, by = corners.bottom
    norm = math.hypot(cx - bx, cy - by)
    if norm < 1e-6:
        return bx, by
    f = cfg.border.safe_tap_inset_px / norm
    return bx + (cx - bx) * f, by + (cy - by) * f


def _position_origin(
    device: RadarDevice,
    cfg: RadarConfig,
    point: GridPoint,
    debug_dir: Path | None = None,
) -> dict:
    """Tap-teleport toward the route start, then verify-and-correct with swipes.

    With the border servo enabled, the tap goes to a SAFE interior point (see
    :func:`_servo_safe_tap_point`) and the approach to the corner is
    closed-loop from there: measured steps that stop on the visible border,
    with the X where the yellow lines cross steered onto the frame's target
    point — so the first capture provably shows the map's bottom corner.
    Without the servo, the tap goes to the start cell itself and
    ``bottom_descend_screens`` pans a fixed amount further down — by swipes,
    which cross the kingdom border freely (a tap below the vertex teleports
    into the neighbouring state). The minimap rect is NOT re-verified after
    descending — beyond the diamond it clamps and would only mislead the
    correction loop.
    """
    gl = cfg.grid_limit
    bottom_anchor = gl is not None and gl.anchor == "bottom"
    servo = bottom_anchor and cfg.border.servo
    tap_x, tap_y = _servo_safe_tap_point(cfg) if servo else (point.x, point.y)
    _wait_touch_clear(device, cfg, tap_x, tap_y)
    device.tap(tap_x, tap_y)
    corrections = 0
    landed: tuple[float, float] | None = None
    for _ in range(ORIGIN_MAX_CORRECTIONS):
        time.sleep(cfg.timings.post_tap_delay_ms / 1000.0)
        frame, _stable = wait_stable(device, cfg)
        rect = _viewport_rect(frame, cfg)
        if rect is None:
            landed = None
            logger.warning("radar: origin check — viewport rect not found on the minimap")
            break
        landed = (rect.cx, rect.cy)
        if not rect.clipped:
            # The whole px<->tile geometry hangs off the calibrated zoom —
            # verify it on the first trustworthy reading and fail fast.
            dev = max(
                abs(rect.w - cfg.viewport.rect_w) / cfg.viewport.rect_w,
                abs(rect.h - cfg.viewport.rect_h) / cfg.viewport.rect_h,
            )
            if dev > ZOOM_SIZE_TOLERANCE:
                msg = (
                    f"zoom mismatch: the minimap viewport rect reads {rect.w}x{rect.h}, "
                    f"calibration expects {cfg.viewport.rect_w}x{cfg.viewport.rect_h} — "
                    "reset the camera zoom (or recalibrate) and rescan"
                )
                raise ScanAborted(msg)
        else:
            # Display-clamped reading: the rect is clipped by the minimap
            # widget, its position is a lie — do not steer by it.
            logger.warning("radar: origin check — viewport rect is clipped, reading untrusted")
            break
        dx, dy = tap_x - rect.cx, tap_y - rect.cy
        if math.hypot(dx, dy) <= ORIGIN_TOLERANCE_PX:
            break
        corrections += 1
        logger.info(
            "radar: origin off target by (%.0f, %.0f) minimap px — correcting with a swipe",
            dx, dy,
        )
        _swipe_relative(device, cfg, dx, dy)
    # With the servo on, the approach is closed-loop from the very first step
    # (measured, stops on the border) — a fixed blind pan would just risk
    # sailing past the corner into the neighbouring state. It remains the
    # servo-off fallback for getting the border into the first frame at all.
    descend = gl.bottom_descend_screens if bottom_anchor and not servo else 0.0
    descend_swipes: list[dict[str, int]] = []
    if descend > 0:
        logger.info(
            "radar: descending %.2f screen(s) below the start cell to reach the border corner",
            descend,
        )
        descend_swipes = _swipe_relative(device, cfg, 0.0, descend * cfg.viewport.rect_h)
    meta = {
        "mode": "tap",
        "origin": True,
        "target_px": [round(tap_x, 2), round(tap_y, 2)],
        "grid_start_px": [round(point.x, 2), round(point.y, 2)],
        "landed_px": [round(landed[0], 2), round(landed[1], 2)] if landed else None,
        "corrections": corrections,
        "descend_screens": descend,
        "descend_swipes": descend_swipes,
    }
    if servo:
        meta.update(_servo_to_border(device, cfg, debug_dir))
    return meta


def _move_to_point(
    device: RadarDevice,
    cfg: RadarConfig,
    previous: GridPoint | None,
    point: GridPoint,
    calib: SwipeCalibration | None = None,
    ref_frame: np.ndarray | None = None,
    debug_dir: Path | None = None,
) -> dict:
    if cfg.navigation.mode == "tap":
        _wait_touch_clear(device, cfg, point.x, point.y)
        device.tap(point.x, point.y)
        return {"mode": "tap", "target_px": [round(point.x, 2), round(point.y, 2)]}
    if previous is None:
        # Position for the first capture at the route's bottom-center start.
        # Tap residual is harmless for stitching (no frame captured yet), but
        # the teleport itself is verified against the minimap viewport rect
        # and corrected with swipes — the game does not honour the tap target.
        return _position_origin(device, cfg, point, debug_dir)
    dx = point.x - previous.x
    dy = point.y - previous.y
    dx, dy, border_meta = _border_swipe_guard(cfg, ref_frame, dx, dy)
    swipes = _swipe_relative(device, cfg, dx, dy, calib)
    meta = {
        "mode": "swipe",
        "from": [previous.ix, previous.iy],
        "delta_minimap_px": [round(dx, 2), round(dy, 2)],
        "swipes": swipes,
    }
    if border_meta is not None:
        meta["border_guard"] = border_meta
    return meta


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
    """
    b = cfg.border
    if not b.block_crossing or frame is None:
        return minimap_dx, minimap_dy, None
    c = cfg.crop
    viewport_w = cfg.stitch_viewport.w if cfg.stitch_viewport is not None else c.w
    viewport_h = cfg.stitch_viewport.h if cfg.stitch_viewport is not None else c.h
    cam_dx = (minimap_dx / cfg.viewport.rect_w) * viewport_w
    cam_dy = (minimap_dy / cfg.viewport.rect_h) * viewport_h
    dist = border_cross_distance(
        frame, c.model_dump(), cam_dx, cam_dy, corridor_px=b.cross_corridor_px,
    )
    if dist is None:
        return minimap_dx, minimap_dy, None
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


