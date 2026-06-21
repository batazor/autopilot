"""Main scan loop: walk the minimap grid → frames + ``manifest.json``.

Default camera movement is relative swipes between grid cells: minimap
tap-teleports proved imprecise (the game clamps/quantizes the jump), while
swipe drift is harmless — the stitcher measures the real frame offsets from
ORB features afterwards, so navigation only needs to land *near* each cell
with enough overlap. ``navigation.mode: tap`` remains available.

The movement/capture primitives live in :mod:`modules.radar.scanner_navigation`
and the origin servo in :mod:`modules.radar.scanner_servo`; this module is the
orchestrator. Manifest IO + the shared ``ScanAborted`` live in
:mod:`modules.radar.manifest`. Several names below are re-exported so the
historical ``from modules.radar.scanner import …`` surface stays stable.
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

import cv2

from modules.radar.border import (
    border_outside_fraction,
    find_border_cross,
    top_border_visible,
)
from modules.radar.config import load_config
from modules.radar.device import RadarDevice, ScanStopped, pick_serial
from modules.radar.geometry import (
    Affine,
    extend_grid_below,
    generate_grid,
    generate_raster_grid,
    limit_grid_centered,
    limit_grid_from_bottom,
    scale_corners,
    scan_walk_from_bottom,
)
from modules.radar.manifest import (  # noqa: F401  (MANIFEST_NAME/ScanAborted/frame_* re-exported)
    MANIFEST_NAME,
    ScanAborted,
    frame_filename,
    frame_key,
    load_manifest,
    save_manifest,
)
from modules.radar.scanner_navigation import (  # noqa: F401  (re-exported for the historical scanner.* surface)
    SwipeCalibration,
    _border_swipe_guard,
    _load_prior_calibration,
    _patch_is_white,
    _swipe_relative,
    _viewport_rect,
    _viewport_rect_center,
    _wait_touch_clear,
    wait_stable,
)
from modules.radar.scanner_servo import (  # noqa: F401  (re-exported)
    _position_origin,
    capture_corner_reference,
)
from modules.radar.stitch import frames_consistent, move_prior

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

    from modules.radar.config import RadarConfig
    from modules.radar.events import RadarEventPublisher
    from modules.radar.geometry import GridPoint

logger = logging.getLogger(__name__)


def build_scan_grid(cfg: RadarConfig) -> list[GridPoint]:
    """Scan route — single source of truth for scanner + API.

    Plain serpentine raster for both modes: every move *between captures* is
    a single grid step, which is what keeps neighbouring frames overlapping
    for ORB registration. The camera starts at the minimap center, and the
    (possibly long) positioning move to the first cell happens *before* the
    first capture, so its accuracy never matters for stitching.
    """
    # Raster mode: views without a minimap world grid (city / island). Cell
    # (0,0) is the current view (captured in place); the route walks right+down
    # by swipes. Absolute x/y only feed deltas, so the crop center is a fine
    # notional start. Replaces the diamond grid + grid_limit.
    if cfg.raster is not None:
        r = cfg.raster
        start = (cfg.crop.x + cfg.crop.w / 2.0, cfg.crop.y + cfg.crop.h / 2.0)
        return generate_raster_grid(start, r.cols, r.rows, r.step_x_px, r.step_y_px)

    # Enlarge the route to the true kingdom — the minimap diamond only spans a
    # central sub-region (origin/affine keep the unscaled diamond).
    corners = scale_corners(cfg.minimap.corners.as_geometry(), cfg.grid_scale)
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
    if cfg.raster is not None:
        # City/island: grab ONE frame immediately. Right after a swipe the game
        # overlays building NAME labels (white text on a dark plate) — the
        # strongest, most stable features on an otherwise low-texture snow base,
        # and tied to buildings. ``wait_stable`` would instead wait for the view
        # to settle, by which point the labels have faded; capturing now anchors
        # ORB on them. The frame is kept regardless of registration (coverage
        # over a perfect seam): we try the swipe-prior match first, then an
        # unconstrained one, and withhold the offset from calibration if neither
        # fits — the stitcher then resolves the cell from the grid + neighbours.
        frame = device.capture()
        if prev_frame is None:
            return frame, True, None
        crop = cfg.crop.model_dump()
        measured = frames_consistent(prev_frame, frame, crop, expected)
        if measured is None:
            measured = frames_consistent(prev_frame, frame, crop, None)
        return frame, True, measured
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
    if cfg.raster is not None:
        # Raster scans (city/island) value COVERAGE over a perfect seam, and
        # the view never zooms (static base) — so a non-registering pair is a
        # thin/low-texture overlap (e.g. a band of open snow), not a torn view.
        # Keep the frame and let the stitcher resolve its position from the grid
        # prior + other neighbours; the untrusted offset is withheld. This is
        # the opposite policy to the world map, where a non-registering frame
        # means an accidental zoom and aborting is correct.
        logger.warning(
            "radar: %s does not register against its predecessor — raster keeps "
            "it (position resolved by the stitcher), not aborting",
            reject_path.name if reject_path else "frame",
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



def run_scan(
    config_path: Path,
    out_dir: Path,
    *,
    serial: str | None = None,
    adb_bin: str | None = None,
    events: RadarEventPublisher | None = None,
    target: str = "global_map",
) -> None:
    # ``target`` selects which game view this config maps; it is recorded on the
    # config (and thus the manifest) so runs can be filtered per map tab.
    cfg = load_config(config_path, target=target)
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
    # Scale to match the enlarged grid so the move-prior predicts the right
    # inter-cell offset (the origin servo still anchors the real bottom corner).
    affine = Affine.from_corners(scale_corners(corners, cfg.grid_scale), cfg.game_size)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out_dir, cfg, grid)
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
    frames: dict = manifest["frames"]
    walk = build_scan_walk(cfg, grid)
    calib = None
    if cfg.navigation.swipe_autoscale:
        calib = _load_prior_calibration(out_dir) or SwipeCalibration()
    crop_dict = cfg.crop.model_dump()

    done = 0
    skipped = 0
    unreached = 0
    unstable = 0
    total = len(grid)
    # Resume: some — but not all — grid cells already have a frame on disk. The
    # camera position on a resumed run is unknown and the route navigates by
    # RELATIVE swipes, so the missing cells cannot just be captured from here.
    # Instead re-anchor at the origin and re-walk the whole route, physically
    # MOVING the camera through already-captured cells (skipping only their
    # recapture) so new frames land in the right place and stitch onto the
    # existing ones. A *complete* run still fast-replays without moving.
    done_present = sum(
        1
        for p in grid
        if frame_key(p.ix, p.iy) in frames and (out_dir / frame_filename(p.ix, p.iy)).is_file()
    )
    resuming = 0 < done_present < total
    if resuming:
        logger.info(
            "radar: resuming — %d/%d cells already captured; re-anchoring at the "
            "origin and re-walking (moving through done cells, capturing the rest)",
            done_present, total,
        )
    previous: GridPoint | None = None
    # Row where the kingdom's top corner entered the view: finish that row for
    # full coverage, then end the scan — it is complete, not interrupted.
    top_border_row: int | None = None
    # Frames captured this session, by cell. The walk always steps to a grid
    # neighbour, so a new frame registers against the cell it arrived from —
    # which holds even when the route backtracks through earlier cells.
    captured_frames: dict[tuple[int, int], np.ndarray] = {}
    # Honest position bookkeeping across guarded moves. The border guard can
    # shorten or zero a move; the route advances regardless, so without this
    # the manifest would record duplicate frames under unvisited cells (it
    # did: the corner runs filled bottom rows with copies of one view).
    # ``carry`` is route-target minus camera (minimap px) and tops up the next
    # command; ``anchor_frame`` is the frame at the camera's last framed
    # position; the swipe lists accumulate physical motion for registration
    # priors — against the anchor (view guard) and the previous manifest
    # entry (the stitcher's consecutive pair).
    carry = (0.0, 0.0)
    anchor_frame: np.ndarray | None = None
    since_anchor: list[dict] = []
    since_capture: list[dict] = []
    # Desired camera travel (screen px) accumulated since the anchor — the
    # calibration target the measured offset is compared against.
    cam_since_anchor = (0.0, 0.0)
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

        if capture and already and not resuming:
            # Complete re-run (every cell already present): fast replay with no
            # movement — the UI progress diamond prefills and the frames are
            # reused as-is. (On a *resume* the camera must instead move through
            # this cell; that path is handled after the move below.)
            skipped += 1
            previous = point
            if events is not None:
                events.frame_done(
                    point.ix,
                    point.iy,
                    unstable=bool(frames[key].get("unstable")),
                    done=done + skipped,
                    total=total,
                )
            continue

        # Guard + registration reference: the camera's last framed position.
        # The walk steps to grid neighbours, so this is normally the cell just
        # stepped off — but after a guarded skip the camera is still standing
        # near the older anchor, which therefore stays the valid reference.
        step = (point.x - previous.x, point.y - previous.y) if previous else (0.0, 0.0)
        move_meta = _move_to_point(
            device, cfg, previous, point, calib, anchor_frame, out_dir, carry,
        )
        previous = point
        if move_meta["mode"] == "tap":
            # Absolute positioning (origin teleport or tap navigation): the
            # camera is wherever the tap put it — carried error and swipe
            # accumulators do not survive a teleport. The anchor frame stays:
            # adjacent-cell teleports keep enough overlap for the view guard
            # (it just runs unconstrained — no swipe prior for a teleport).
            carry = (0.0, 0.0)
            since_anchor = []
            since_capture = []
            cam_since_anchor = (0.0, 0.0)
            reached = True
        else:
            carry = tuple(move_meta.get("shortfall_minimap_px") or (0.0, 0.0))
            since_anchor += move_meta["swipes"]
            since_capture += move_meta["swipes"]
            cam = move_meta.get("camera_px") or (0.0, 0.0)
            cam_since_anchor = (cam_since_anchor[0] + cam[0], cam_since_anchor[1] + cam[1])
            step_norm = math.hypot(*step)
            # The camera stands within half a grid step of the route target —
            # close enough that a capture here genuinely shows this cell.
            reached = step_norm < 1e-6 or math.hypot(*carry) <= 0.5 * step_norm

        if capture and already:
            # Resuming: the camera has now physically moved through this
            # already-captured cell, so the route stays tracked. Skip the
            # recapture, but anchor the next NEW frame against this one (loaded
            # from disk) so it registers cleanly onto the existing map.
            skipped += 1
            if reached:
                done_frame = cv2.imread(str(out_dir / filename))
                if done_frame is not None:
                    captured_frames[(point.ix, point.iy)] = done_frame
                    anchor_frame = done_frame
                    since_anchor = []
                    since_capture = []
                    cam_since_anchor = (0.0, 0.0)
            if events is not None:
                events.frame_done(
                    point.ix,
                    point.iy,
                    unstable=bool(frames[key].get("unstable")),
                    done=done + skipped,
                    total=total,
                )
            continue

        if not capture:
            # Backtrack step: re-walk an already-captured cell to reach an
            # unvisited branch — move the camera, capture nothing. Arriving at
            # a framed cell resyncs the registration anchor.
            if reached and (point.ix, point.iy) in captured_frames:
                anchor_frame = captured_frames[(point.ix, point.iy)]
                since_anchor = []
                cam_since_anchor = (0.0, 0.0)
            continue

        if not reached:
            # The border guard refused (most of) the move: the cell is beyond
            # or at the kingdom border and the camera never got there. Record
            # the skip instead of capturing a duplicate frame under a wrong
            # cell label — those duplicates fed the stitcher contradictory
            # edges and warped the whole canvas.
            unreached += 1
            logger.warning(
                "radar: cell %s unreachable (border guard left the camera "
                "%.0f minimap px short) — skipping, no frame recorded",
                key, math.hypot(*carry),
            )
            cells = manifest.setdefault("skipped_cells", [])
            if not any(s.get("ix") == point.ix and s.get("iy") == point.iy for s in cells):
                cells.append({
                    "ix": point.ix,
                    "iy": point.iy,
                    "shortfall_minimap_px": [round(carry[0], 2), round(carry[1], 2)],
                })
                save_manifest(out_dir, manifest)
            continue

        time.sleep(cfg.timings.post_tap_delay_ms / 1000.0)
        # Swipe prior = ALL physical motion since the anchor frame (several
        # moves when guarded skips intervened); a teleport has no swipe prior.
        expected = (
            None
            if move_meta["mode"] == "tap"
            else move_prior({"move": {"mode": "swipe", "swipes": since_anchor}})
        )
        guard_ref = anchor_frame
        vw = cfg.stitch_viewport.w if cfg.stitch_viewport is not None else cfg.crop.w
        vh = cfg.stitch_viewport.h if cfg.stitch_viewport is not None else cfg.crop.h
        if expected is not None and (abs(expected[0]) > 0.7 * vw or abs(expected[1]) > 0.7 * vh):
            # Accumulated travel since the anchor approaches a full viewport:
            # too little overlap left for registration, so a failed match
            # would prove nothing about zoom — capture unguarded rather than
            # abort the scan on a guaranteed-unmatchable pair. The next
            # single-step capture restores the guard.
            logger.info(
                "radar: %s sits (%.0f, %.0f) px from the anchor view — overlap "
                "too thin for the view guard, capturing unguarded",
                key, expected[0], expected[1],
            )
            guard_ref = None
            expected = None
        frame, stable, measured = _guarded_capture(
            device, cfg, guard_ref, expected,
            reject_path=out_dir / f"rejected_{key}.png",
        )
        if cfg.border.servo and border_outside_fraction(frame, crop_dict) >= cfg.border.gap_back_off_frac:
            # The camera crossed the border mid-walk despite the pre-swipe
            # guards — a scan of the neighbouring state is garbage; stop with
            # the frame as evidence rather than keep capturing. Border-only:
            # raster views (city/island) have no kingdom edge, and dark water
            # around the base would otherwise trip this falsely.
            evidence = out_dir / f"outside_{key}.png"
            cv2.imwrite(str(evidence), frame)
            msg = (
                f"frame {key} is outside the kingdom (camera crossed the border "
                f"mid-walk) — evidence saved to {evidence.name}"
            )
            raise ScanAborted(msg)
        if calib is not None and expected is not None and measured is not None:
            # Feed the DESIRED camera travel, not the commanded finger travel
            # (= ``expected``): the finger already includes the current scale,
            # so its ratio to measured is the constant raw gain and the EMA
            # would diverge geometrically (observed: 1.0 → 1.53 in one scan).
            calib.update(cam_since_anchor, measured)
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
        anchor_frame = frame
        since_anchor = []
        cam_since_anchor = (0.0, 0.0)
        if not stable:
            unstable += 1
        manifest.setdefault("frame_size", {"w": int(frame.shape[1]), "h": int(frame.shape[0])})

        # Save the frame as-is (no UI crop): one coordinate system for capture
        # and stitch. The HUD bakes into tiles for now — accepted trade-off
        # while the placement geometry is being tuned.
        if not cv2.imwrite(str(out_dir / filename), frame):
            msg = f"failed to write {out_dir / filename}"
            raise ScanAborted(msg)
        if move_meta["mode"] != "tap":
            # The entry's swipes describe the displacement from the PREVIOUS
            # ENTRY (the stitcher's consecutive pair) — across guarded skips
            # and backtracks that is several moves, not just the last one.
            move_meta = {**move_meta, "swipes": since_capture}
        since_capture = []
        entry = {
            "ix": point.ix,
            "iy": point.iy,
            # Capture sequence index, persisted so the stitcher's consecutive-pair
            # matching never depends on dict iteration order (the JSON object key
            # order is not a guaranteed contract). Each entry's ``move`` covers
            # the motion FROM the previous capture, so ``order-1 -> order`` is
            # the highest-overlap pair. On resume new frames continue the sequence.
            "order": len(frames),
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
        save_manifest(out_dir, manifest)
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
        "scan complete: %d captured, %d already present, %d unreachable past "
        "the border, %d unstable → %s",
        done,
        skipped,
        unreached,
        unstable,
        out_dir,
    )
    return False



def _move_to_point(
    device: RadarDevice,
    cfg: RadarConfig,
    previous: GridPoint | None,
    point: GridPoint,
    calib: SwipeCalibration | None = None,
    ref_frame: np.ndarray | None = None,
    debug_dir: Path | None = None,
    carry: tuple[float, float] = (0.0, 0.0),
) -> dict:
    """One route step. ``carry`` is the unachieved travel from earlier guarded
    moves (minimap px) — added to this step so the command targets the cell's
    absolute position instead of stepping from a phantom one. The returned
    meta's ``shortfall_minimap_px`` is the new carry: planned minus what the
    border guard let through."""
    if cfg.raster is not None and previous is None:
        # Raster start: capture wherever the camera already is — no tap (a tap on
        # the city would select a building) and no minimap origin servo. The
        # stitcher anchors the canvas on this first frame; later cells swipe from
        # it. Zero commanded travel, so empty swipe/camera accumulators.
        return {"mode": "start", "swipes": [], "camera_px": [0.0, 0.0]}
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
    dx = point.x - previous.x + carry[0]
    dy = point.y - previous.y + carry[1]
    dx_done, dy_done, border_meta = _border_swipe_guard(cfg, ref_frame, dx, dy)
    swipes = _swipe_relative(device, cfg, dx_done, dy_done, calib)
    vw = cfg.stitch_viewport.w if cfg.stitch_viewport is not None else cfg.crop.w
    vh = cfg.stitch_viewport.h if cfg.stitch_viewport is not None else cfg.crop.h
    meta = {
        "mode": "swipe",
        "from": [previous.ix, previous.iy],
        "delta_minimap_px": [round(dx_done, 2), round(dy_done, 2)],
        "shortfall_minimap_px": [round(dx - dx_done, 2), round(dy - dy_done, 2)],
        # DESIRED camera travel in screen px (pre-calibration target). This —
        # not the commanded finger travel — is what swipe calibration compares
        # against the measured offset: the finger already carries the current
        # scale, and its ratio to measured is the game's constant raw gain,
        # which fed back as-is makes the scale diverge geometrically. Raster
        # deltas are already screen px (no minimap diamond to scale through).
        "camera_px": (
            [round(dx_done, 1), round(dy_done, 1)]
            if cfg.raster is not None
            else [
                round((dx_done / cfg.viewport.rect_w) * vw, 1),
                round((dy_done / cfg.viewport.rect_h) * vh, 1),
            ]
        ),
        "swipes": swipes,
    }
    if border_meta is not None:
        meta["border_guard"] = border_meta
    return meta

