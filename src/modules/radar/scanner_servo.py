"""Origin positioning + the border-line servo.

The minimap tap-teleport is quantized and untrusted, so the scan's first frame
is locked onto what the camera actually sees: the X where the two dashed yellow
border lines cross (the kingdom's bottom corner). This module owns that
closed-loop approach — measure the crossing, swipe, repeat — plus the corner
reference calibration the servo verifies against. It builds on the capture and
swipe primitives in :mod:`modules.radar.scanner_navigation`.
"""

from __future__ import annotations

import logging
import math
import time
from typing import TYPE_CHECKING

import cv2

from modules.radar.border import (
    border_band_y,
    border_outside_fraction,
    border_outside_top_y,
    find_border_cross,
    find_border_lines,
)
from modules.radar.config import CornerRefConfig
from modules.radar.geometry import diamond_center
from modules.radar.manifest import ScanAborted
from modules.radar.scanner_navigation import (
    _swipe_fingers,
    _swipe_relative,
    _viewport_rect,
    _wait_touch_clear,
    wait_stable,
)
from modules.radar.stitch import frames_consistent

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

    from modules.radar.config import RadarConfig
    from modules.radar.device import RadarDevice
    from modules.radar.geometry import GridPoint

logger = logging.getLogger(__name__)


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
# Movement feedback: a swipe achieving less than this share of its commanded
# travel is being eaten by the game (rubber-band/pan clamp)...
SERVO_MIN_MOVE_EFFECT = 0.3
# ...and this many underperforming downward moves in a row = the descend is
# clamped: stop marching, switch to lateral search for the corner.
SERVO_CLAMP_STRIKES = 2


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
        # NOTE: the pin's size cannot verify zoom — it is a fixed-size graphic
        # at every zoom level (a size-based check here once falsely killed
        # scans whenever overlapping markers nibbled the ring).
        if rect.clipped:
            # Display-clamped reading: the pin is clipped by the minimap
            # widget, its position is a lie — do not steer by it.
            logger.warning("radar: origin check — viewport pin is clipped, reading untrusted")
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

