"""Scan navigation: swipe math + route building (swipe default, tap optional)."""

import json
import math
from itertools import pairwise

import cv2
import numpy as np
import pytest

from modules.radar.config import (
    BorderConfig,
    CornerRefConfig,
    CornersConfig,
    CropConfig,
    GridLimitConfig,
    LabelGuardConfig,
    MinimapConfig,
    NavigationConfig,
    RadarConfig,
    StitchViewportConfig,
    ViewportConfig,
)
from modules.radar.scanner import (
    ScanAborted,
    SwipeCalibration,
    _border_swipe_guard,
    _load_prior_calibration,
    _move_to_point,
    _patch_is_white,
    _position_origin,
    _swipe_relative,
    _viewport_rect,
    _viewport_rect_center,
    _wait_touch_clear,
    build_scan_grid,
    capture_corner_reference,
)


def _grey_frame() -> np.ndarray:
    """A clear (non-white) screen — the label guard passes immediately."""
    return np.full((1280, 720, 3), 128, dtype=np.uint8)


class FakeDevice:
    def __init__(self, frames: list[np.ndarray] | None = None) -> None:
        self.swipes: list[tuple[float, float, float, float, int]] = []
        self.taps: list[tuple[float, float]] = []
        # Screens returned by successive capture() calls; the last repeats once
        # exhausted so the label guard always sees a final clear frame.
        self._frames = list(frames) if frames else [_grey_frame()]
        self.captures = 0

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None:
        self.swipes.append((x1, y1, x2, y2, duration_ms))

    def tap(self, x: float, y: float) -> None:
        self.taps.append((x, y))

    def capture(self) -> np.ndarray:
        self.captures += 1
        idx = min(self.captures - 1, len(self._frames) - 1)
        return self._frames[idx]


def _cfg(
    grid_limit: GridLimitConfig | None = None,
    navigation: NavigationConfig | None = None,
    label_guard: LabelGuardConfig | None = None,
    border: BorderConfig | None = None,
) -> RadarConfig:
    return RadarConfig(
        minimap=MinimapConfig(
            bbox=(0, 0, 200, 200),
            corners=CornersConfig(
                top=(100, 0),
                right=(200, 100),
                bottom=(100, 200),
                left=(0, 100),
            ),
        ),
        viewport=ViewportConfig(rect_w=24, rect_h=39),
        crop=CropConfig(x=0, y=156, w=620, h=940),
        stitch_viewport=StitchViewportConfig(w=720, h=1185),
        grid_limit=grid_limit,
        navigation=navigation or NavigationConfig(),
        label_guard=label_guard or LabelGuardConfig(),
        border=border or BorderConfig(),
    )


def test_swipe_relative_inverts_camera_delta_and_stays_inside_crop() -> None:
    device = FakeDevice()

    emitted = _swipe_relative(device, _cfg(), minimap_dx=15.6, minimap_dy=25.35)

    assert emitted == [
        {"x1": 572, "y1": 1048, "x2": 104, "y2": 278, "ms": 450},
    ]
    assert device.swipes == [(572, 1048, 104, 278, 450)]


def _frame_with_border_right_of_center(cfg: RadarConfig, offset_px: int) -> np.ndarray:
    """Grey screen with a dashed yellow border ``offset_px`` right of the crop
    center and the neutral-dark inter-kingdom gap beyond it — a move is a
    crossing only when the gap itself lies on the path."""
    frame = _grey_frame()
    cx = cfg.crop.x + cfg.crop.w // 2
    frame[:, cx + offset_px + 8 :] = (40, 42, 50)
    for y in range(cfg.crop.y, cfg.crop.y + cfg.crop.h, 16):
        cv2.line(frame, (cx + offset_px, y), (cx + offset_px, y + 8), (120, 230, 235), 4)
    return frame


def test_border_swipe_guard_shortens_a_crossing_move() -> None:
    cfg = _cfg()
    frame = _frame_with_border_right_of_center(cfg, 200)

    # One viewport-width to the right (≈720 px of camera travel) — past the line.
    dx, dy, meta = _border_swipe_guard(cfg, frame, cfg.viewport.rect_w, 0.0)

    assert meta is not None
    assert meta["travel_scale"] < 1.0
    travel = (dx / cfg.viewport.rect_w) * cfg.stitch_viewport.w
    # Stops cross_margin short of the crossing (the vertical test line cannot
    # be Hough-fitted, so the stop line is the dark gap edge at ~208 px).
    assert 0.0 <= travel <= 208 - cfg.border.cross_margin_px + 15
    assert dy == 0.0


def test_border_swipe_guard_leaves_safe_moves_alone() -> None:
    cfg = _cfg()
    frame = _frame_with_border_right_of_center(cfg, 200)

    # Moving left, away from the line — untouched.
    assert _border_swipe_guard(cfg, frame, -cfg.viewport.rect_w, 0.0) == (
        -cfg.viewport.rect_w, 0.0, None,
    )
    # Plain terrain — untouched.
    assert _border_swipe_guard(cfg, _grey_frame(), 10.0, 5.0) == (10.0, 5.0, None)
    # No reference frame yet (first move / resumed gap) — untouched.
    assert _border_swipe_guard(cfg, None, 10.0, 5.0) == (10.0, 5.0, None)


def test_swipe_relative_chunks_long_moves(monkeypatch) -> None:
    import modules.radar.scanner as scanner_mod

    pauses: list[float] = []
    monkeypatch.setattr(scanner_mod.time, "sleep", pauses.append)
    device = FakeDevice()

    emitted = _swipe_relative(device, _cfg(), minimap_dx=60.0, minimap_dy=0.0)

    assert len(emitted) > 1  # finger travel > crop width → split into chunks
    assert len(device.swipes) == len(emitted)
    # Anti-double-tap: a pause separates every consecutive chunk pair, or the
    # game reads two quick touches as the zoom gesture.
    assert pauses == [0.5] * (len(emitted) - 1)
    c = _cfg().crop
    for x1, y1, x2, y2, _ms in device.swipes:
        for x in (x1, x2):
            assert c.x <= x <= c.x + c.w
        for y in (y1, y2):
            assert c.y <= y <= c.y + c.h


def test_build_scan_grid_is_serpentine_single_steps() -> None:
    cfg = _cfg()  # swipe is the default mode
    grid = build_scan_grid(cfg)

    assert grid == build_scan_grid(cfg)  # deterministic
    rows: dict[int, list[int]] = {}
    for p in grid:
        rows.setdefault(p.iy, []).append(p.ix)
    for iy, ixs in rows.items():
        assert ixs == sorted(ixs, reverse=iy % 2 == 1)  # serpentine raster
    # Within a row every capture-to-capture move is exactly one grid step —
    # that is what keeps neighbouring frames overlapping for registration.
    for a, b in pairwise(grid):
        if a.iy == b.iy:
            assert abs(b.ix - a.ix) == 1


def test_first_move_taps_the_minimap_start_cell(monkeypatch) -> None:
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    device = FakeDevice()
    cfg = _cfg()
    grid = build_scan_grid(cfg)

    meta = _move_to_point(device, cfg, None, grid[0])

    # The pre-capture positioning move teleports by tapping the minimap straight
    # at the start cell. No viewport rect is visible on the fake screen, so the
    # verify loop reads nothing and no correction swipe is emitted.
    assert meta["mode"] == "tap"
    assert meta["origin"] is True
    assert meta["target_px"] == [round(grid[0].x, 2), round(grid[0].y, 2)]
    assert meta["landed_px"] is None
    assert device.taps == [(grid[0].x, grid[0].y)]
    assert device.swipes == []


def _rect_frame(x: float, y: float) -> np.ndarray:
    """A screen with the white minimap viewport rect (24×39) centered on (x, y)."""
    frame = _grey_frame()
    cv2.rectangle(
        frame,
        (int(x) - 12, int(y) - 19),
        (int(x) + 12, int(y) + 19),
        (255, 255, 255),
        2,
    )
    return frame


def test_viewport_rect_center_reads_the_white_rect() -> None:
    cfg = _cfg()  # minimap bbox (0, 0, 200, 200)
    center = _viewport_rect_center(_rect_frame(120.0, 80.0), cfg)
    assert center is not None
    assert center[0] == pytest.approx(120.0, abs=1.5)
    assert center[1] == pytest.approx(80.0, abs=1.5)
    assert _viewport_rect_center(_grey_frame(), cfg) is None


def test_position_origin_descends_below_the_start_cell(monkeypatch) -> None:
    """With bottom_descend_screens set, the origin pans further down by swipes
    after converging — a tap below the vertex would land in the neighbouring
    state, but a swipe crosses the border freely."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(
            anchor="bottom", max_frames=15, bottom_descend_screens=1.25,
        ),
        label_guard=LabelGuardConfig(enabled=False),
        border=BorderConfig(servo=False),  # descend only — the servo is tested separately
    )
    grid = build_scan_grid(cfg)
    target = grid[0]
    on_target = _rect_frame(target.x, target.y)
    device = FakeDevice(frames=[on_target])

    meta = _position_origin(device, cfg, target)

    assert meta["corrections"] == 0
    assert meta["descend_screens"] == 1.25
    assert len(meta["descend_swipes"]) >= 1
    # All descend swipes drag the finger upward (content moves up → camera down).
    for s in meta["descend_swipes"]:
        assert s["y2"] < s["y1"]
        assert s["x2"] == s["x1"]


def _x_frame(cross_x: int, cross_y: int) -> np.ndarray:
    """A screen with the dashed border lines crossing at (cross_x, cross_y) —
    tails extend past the crossing, so the lowest yellow point is NOT the corner."""
    frame = _grey_frame()
    cv2.line(
        frame, (cross_x - 150, cross_y - 150), (cross_x + 60, cross_y + 60), (120, 230, 235), 4,
    )
    cv2.line(
        frame, (cross_x + 150, cross_y - 150), (cross_x - 60, cross_y + 60), (120, 230, 235), 4,
    )
    return frame


def test_position_origin_servos_onto_the_border_cross(monkeypatch) -> None:
    """The origin must end with the line crossing on the frame's target point
    (both axes), steered by what the camera actually sees — not by a blind
    constant. A sideways start offset used to walk a whole column outside."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = cfg.crop.y + cfg.crop.h * cfg.border.target_frac
    grid = build_scan_grid(cfg)
    # Verify loop: no white rect → one wait_stable (3 captures) and break.
    # Servo: measure 1 sees the crossing high and to the left → one corrective
    # swipe; measure 2 sees it on target → lock. 3 captures per measure.
    off_target = _x_frame(220, 400)
    on_target = _x_frame(int(target_x), int(target_y))
    device = FakeDevice(frames=[off_target] * 6 + [on_target] * 3)

    meta = _position_origin(device, cfg, grid[0])

    assert meta["servo_steps"] == 1
    # The corrective swipe moves the content right AND down (one chunked move).
    correction = device.swipes[-1]
    assert correction[2] > correction[0]  # x2 > x1
    assert correction[3] > correction[1]  # y2 > y1
    cross = meta["border_apex_px"]
    assert cross is not None
    assert abs(cross[0] - target_x) <= cfg.border.tolerance_px
    assert abs(cross[1] - target_y) <= cfg.border.tolerance_px


def test_position_origin_servo_recovers_from_an_overshoot(monkeypatch) -> None:
    """Camera already past the corner: the border line sits HIGH in the frame
    and no crossing is visible. The servo must climb back up by the measured
    distance — not keep descending deeper into the neighbouring state."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = cfg.crop.y + cfg.crop.h * cfg.border.target_frac
    # Single border line near the top of the crop — overshoot view.
    overshot = _grey_frame()
    cv2.line(overshot, (60, 250), (560, 500), (120, 230, 235), 4)
    on_target = _x_frame(int(target_x), int(target_y))
    # Verify loop: 3 captures. Servo measure 1: overshot view → one upward
    # correction. Servo measure 2: crossing on target → lock.
    device = FakeDevice(frames=[overshot] * 6 + [on_target] * 3)

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    assert meta["servo_steps"] == 1
    # The recovery swipe drags the content DOWN (camera up, back into the
    # kingdom) — and is sized by the measured band, not a blind step.
    recovery = device.swipes[-1]
    assert recovery[3] > recovery[1]  # y2 > y1
    assert recovery[2] == recovery[0]  # lateral untouched without a crossing
    assert meta["border_apex_px"] is not None


def test_position_origin_servo_slides_along_the_line_to_the_corner(monkeypatch) -> None:
    """Sideways start offset: a single border line is in view at the right
    height, but the crossing is off to the side. The servo must slide downhill
    ALONG the line toward the corner — the failure mode where it used to hold
    the line vertically and burn all its steps without ever finding the X."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = int(cfg.crop.y + cfg.crop.h * cfg.border.target_frac)
    # Down-right line (the lower-left edge) already at the target height:
    # vertical correction is within tolerance, so only sliding can progress.
    along_line = _grey_frame()
    cv2.line(along_line, (0, target_y - 160), (619, target_y + 150), (120, 230, 235), 4)
    on_target = _x_frame(int(target_x), target_y)
    device = FakeDevice(frames=[along_line] * 6 + [on_target] * 3)

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    assert meta["servo_steps"] == 1
    # Downhill along a down-right line: camera moves down-right → the content
    # (finger) is dragged up-left.
    for x1, y1, x2, y2, _ms in device.swipes:
        assert x2 < x1
        assert y2 < y1
    assert meta["border_apex_px"] is not None


def test_position_origin_taps_a_safe_interior_point(monkeypatch) -> None:
    """With the servo on, the origin tap goes to a guaranteed-inside point —
    a quantized/redirected tap near the bare vertex can land across the border
    before any guard sees a single frame."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = int(cfg.crop.y + cfg.crop.h * cfg.border.target_frac)
    device = FakeDevice(frames=[_x_frame(int(target_x), target_y)])

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    # Corners: top (100,0), right (200,100), bottom (100,200), left (0,100) →
    # center (100,100); the tap is safe_tap_inset_px (6) above the vertex.
    assert device.taps == [(100.0, 194.0)]
    assert meta["border_apex_px"] is not None
    assert meta["grid_start_px"] is not None


def test_position_origin_dark_steers_when_the_line_is_undetected(monkeypatch) -> None:
    """Yellow line undetected but the dark out-of-bounds mass is visible below:
    the servo must steer on the dark edge — the EXACT case where it used to
    classify the view as 'no border anywhere' and blind-descend across."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = int(cfg.crop.y + cfg.crop.h * cfg.border.target_frac)
    # Dark gap 200 px below the target height, no yellow anywhere.
    dark_below = _grey_frame()
    dark_below[target_y + 200 :, :] = (40, 42, 50)
    on_target = _x_frame(int(target_x), target_y)
    device = FakeDevice(frames=[dark_below] * 6 + [on_target] * 3)

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    assert meta["servo_steps"] == 1
    assert [t["decision"] for t in meta["servo_trail"]] == ["dark_steer", "lock"]
    # The move is the MEASURED remaining distance (200 px down), not a blind step.
    x1, y1, x2, y2, _ms = device.swipes[-1]
    assert x2 == x1
    assert y1 - y2 == pytest.approx(200, abs=25)


def test_position_origin_climbs_back_out_of_the_gap(monkeypatch) -> None:
    """When the camera sits in the inter-kingdom gap (lower band out-of-bounds),
    the servo must climb BACK toward the kingdom — never descend deeper — even
    when the thin dashed line isn't detected. The robust anti-cross recovery."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = int(cfg.crop.y + cfg.crop.h * cfg.border.target_frac)
    # In the gap: the crop's whole lower band is dark out-of-bounds (connected
    # to the edges). No yellow line at all — the line detector is no help here.
    in_gap = _grey_frame()
    in_gap[cfg.crop.y + cfg.crop.h // 2 :, :] = (40, 42, 50)
    on_target = _x_frame(int(target_x), target_y)
    device = FakeDevice(frames=[in_gap] * 6 + [on_target] * 3)

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    assert meta["servo_steps"] == 1
    # Climbing back: camera up → the finger is dragged DOWN (y2 > y1).
    for _x1, y1, _x2, y2, _ms in device.swipes:
        assert y2 > y1
    assert meta["border_apex_px"] is not None
    assert [t["decision"] for t in meta["servo_trail"]] == ["gap_climb", "lock"]


def _textured_frame(seed: int, with_rect_at: tuple[int, int] | None = None) -> np.ndarray:
    """Snow-grey frame with ORB-matchable grey texture: no yellow, no dark.

    Grey-on-grey shapes keep saturation at zero (never trips the yellow mask)
    and values above the dark threshold (never reads as out-of-bounds), while
    giving ORB plenty of corners so movement feedback can register frames.
    """
    rng = np.random.default_rng(seed)
    frame = np.full((1280, 720, 3), 200, dtype=np.uint8)
    for _ in range(400):
        x, y = int(rng.integers(0, 680)), int(rng.integers(0, 1240))
        s = int(rng.integers(8, 36))
        v = int(rng.integers(110, 185))
        cv2.rectangle(frame, (x, y), (x + s, y + s), (v, v, v), -1)
    if with_rect_at is not None:
        rx, ry = with_rect_at
        cv2.rectangle(frame, (rx - 12, ry - 19), (rx + 12, ry + 19), (255, 255, 255), 2)
    return frame


def test_servo_detects_pan_clamp_and_probes_laterally(monkeypatch) -> None:
    """Swipes that do not move the camera (game rubber-band/clamp) must be
    DETECTED by movement feedback — the servo then searches laterally instead
    of burning its blind budget marching in place (the 202450 failure)."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = int(cfg.crop.y + cfg.crop.h * cfg.border.target_frac)
    frozen = _textured_frame(31)  # identical every capture: nothing moves
    on_target = _x_frame(int(target_x), target_y)
    # verify(3) + 4 servo measures on the frozen view (3 captures each) + lock.
    device = FakeDevice(frames=[frozen] * 15 + [on_target] * 3)

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    decisions = [t["decision"] for t in meta["servo_trail"]]
    assert decisions == ["blind", "blind", "lateral_probe", "lateral_probe", "lock"]
    # Feedback measured the marching-in-place: ~0 effectiveness recorded.
    assert meta["servo_trail"][1]["effectiveness"] is not None
    assert meta["servo_trail"][1]["effectiveness"] < 0.3
    # Probes move sideways along the clamp, never further down.
    lateral = [s for s in device.swipes if s[1] == s[3]]
    assert lateral  # at least one horizontal probe swipe
    downward = [s for s in device.swipes if s[3] < s[1]]  # finger up = camera down
    assert len(downward) == 2  # only the two blind steps before the clamp hit


def test_servo_aligns_to_corner_reference_at_the_clamp(monkeypatch) -> None:
    """With a recorded corner reference, the at-clamp search is guided: the
    servo aligns the (display-clamped) minimap rect x to the recorded reading
    instead of probing blind."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
        border=BorderConfig(),
    )
    cfg.corner_ref = CornerRefConfig(
        cross_px=(310.0, 730.0), rect_px=(120.0, 120.0), rect_size=(24, 39),
    )
    target_x = cfg.crop.x + cfg.crop.w / 2
    target_y = int(cfg.crop.y + cfg.crop.h * cfg.border.target_frac)
    no_rect = _textured_frame(33)
    # Camera reads rect x=100; the reference says the corner reads x=120.
    frozen = _textured_frame(33, with_rect_at=(100, 120))
    on_target = _x_frame(int(target_x), target_y)
    device = FakeDevice(frames=[no_rect] * 3 + [frozen] * 12 + [on_target] * 3)

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    decisions = [t["decision"] for t in meta["servo_trail"]]
    assert decisions == ["blind", "blind", "ref_align", "ref_align", "lock"]
    # Alignment moves the camera RIGHT (toward ref x=120): finger drags left.
    align = [s for s in device.swipes if s[1] == s[3]]
    assert align
    for x1, _y1, x2, _y2, _ms in align:
        assert x2 < x1


def test_servo_targets_the_recorded_corner_view(monkeypatch) -> None:
    """With a corner reference, the lock target is the RECORDED crossing
    position — the operator proved that view reachable. The theoretical
    (center, target_frac) point can sit beyond the pan clamp."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    # Recorded view: the X sits off-center and LOW — not where theory wants it.
    cfg.corner_ref = CornerRefConfig(cross_px=(400.0, 900.0))
    device = FakeDevice(frames=[_x_frame(400, 900)])

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    # Lock on the first measurement: the view already matches the reference.
    assert meta["servo_steps"] == 0
    assert meta["border_apex_px"] == [pytest.approx(400, abs=10), pytest.approx(900, abs=10)]


def test_servo_aborts_at_reference_position_without_a_crossing(monkeypatch) -> None:
    """Camera at the calibrated corner reading but no X in view: a precise,
    actionable abort (recalibrate) — not an endless probe."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    cfg.corner_ref = CornerRefConfig(
        cross_px=(310.0, 730.0), rect_px=(100.0, 120.0), rect_size=(24, 39),
    )
    no_rect = _textured_frame(35)
    frozen = _textured_frame(35, with_rect_at=(100, 120))  # already at ref x
    device = FakeDevice(frames=[no_rect] * 3 + [frozen] * 9)

    with pytest.raises(ScanAborted, match="calibrated corner position"):
        _position_origin(device, cfg, build_scan_grid(cfg)[0])


def test_capture_corner_reference_records_the_view() -> None:
    cfg = _cfg()
    frame = _x_frame(310, 700)
    cv2.rectangle(frame, (100 - 12, 120 - 19), (100 + 12, 120 + 19), (255, 255, 255), 2)

    ref = capture_corner_reference(frame, cfg)

    assert ref.cross_px[0] == pytest.approx(310, abs=10)
    assert ref.cross_px[1] == pytest.approx(700, abs=10)
    assert ref.rect_px is not None
    assert ref.rect_px[0] == pytest.approx(100, abs=2)
    assert ref.rect_size is not None
    # Drawn with 2 px outline: the detected bbox is a hair over 24x39.
    assert ref.rect_size[0] == pytest.approx(24, abs=4)
    assert ref.rect_size[1] == pytest.approx(39, abs=4)

    with pytest.raises(ValueError, match="not detectable"):
        capture_corner_reference(_grey_frame(), cfg)


def test_position_origin_tolerates_a_nibbled_pin(monkeypatch) -> None:
    """The pin is a fixed-size graphic at every zoom — its size can NOT verify
    zoom, and overlapping markers routinely nibble the ring. An undersized
    reading must not abort the scan (a size gate here once falsely killed
    runs with '24x22 vs 24x39')."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(label_guard=LabelGuardConfig(enabled=False))
    grid = build_scan_grid(cfg)
    target = grid[0]
    # Pin with its lower third erased by an overlapping marker.
    nibbled = _rect_frame(target.x, target.y)
    nibbled[int(target.y) + 8 : int(target.y) + 21, :] = 128

    meta = _position_origin(FakeDevice(frames=[nibbled]), cfg, target)

    assert meta["landed_px"] is not None  # read and accepted, no abort


def test_viewport_rect_reports_clipping() -> None:
    cfg = _cfg()  # minimap bbox (0, 0, 200, 200)
    centered = _rect_frame(100.0, 120.0)
    rect = _viewport_rect(centered, cfg)
    assert rect is not None
    assert not rect.clipped
    assert rect.w == pytest.approx(24, abs=4)  # 2 px outline widens the bbox
    assert rect.h == pytest.approx(39, abs=4)

    # Rect pressed against the bbox bottom: the drawing is clipped — position lies.
    at_edge = _rect_frame(100.0, 195.0)
    rect = _viewport_rect(at_edge, cfg)
    assert rect is not None
    assert rect.clipped


def test_position_origin_aborts_when_the_crossing_never_appears(monkeypatch) -> None:
    """No dashed-line X in view after max_steps → the scan must not start
    blind (a single side line or empty terrain cannot fake an origin lock)."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
    )
    device = FakeDevice()  # plain grey screens: no border anywhere

    with pytest.raises(ScanAborted):
        _position_origin(device, cfg, build_scan_grid(cfg)[0])


def test_position_origin_servo_gives_up_when_cross_not_required(monkeypatch) -> None:
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
        # Loose blind cap so max_steps is the binding limit here.
        border=BorderConfig(require_cross=False, max_blind_screens=6.0),
    )
    device = FakeDevice()  # plain grey screens: no border anywhere

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    assert meta["border_apex_px"] is None
    assert meta["servo_steps"] == cfg.border.max_steps  # kept descending, then gave up


def test_position_origin_stops_blind_descend_before_crossing_the_vertex(monkeypatch) -> None:
    """A blind descend that never reveals the border has crossed the kingdom
    vertex into the next state. It must stop at the blind cap — NOT keep
    marching deeper — well before max_steps would be reached."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(
        grid_limit=GridLimitConfig(anchor="bottom", max_frames=15),
        label_guard=LabelGuardConfig(enabled=False),
        border=BorderConfig(
            require_cross=False, max_steps=50,
            approach_step_screens=0.3, max_blind_screens=1.5,
        ),
    )
    device = FakeDevice()  # grey: border never appears → pure blind descend

    meta = _position_origin(device, cfg, build_scan_grid(cfg)[0])

    viewport_h = cfg.stitch_viewport.h
    expected_steps = math.ceil(
        cfg.border.max_blind_screens / cfg.border.approach_step_screens,
    )
    assert meta["servo_steps"] == expected_steps  # blind cap, not max_steps=50
    # Total blind travel never exceeds the cap by more than one step.
    # FakeDevice records swipes as (x1, y1, x2, y2, duration_ms) tuples.
    descended = sum(abs(y1 - y2) for _x1, y1, _x2, y2, _ms in device.swipes)
    assert descended <= (cfg.border.max_blind_screens + cfg.border.approach_step_screens) * viewport_h


def test_position_origin_corrects_a_redirected_teleport(monkeypatch) -> None:
    """The game does not honour the minimap tap target (observed teleporting to
    the right corner) — the origin loop must read the real landing off the
    minimap and swipe the residual toward the target."""
    import modules.radar.scanner as scanner_mod

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(label_guard=LabelGuardConfig(enabled=False))
    grid = build_scan_grid(cfg)
    # An interior cell: near the bbox edge the pin reads as clipped (untrusted)
    # and the correction loop rightly refuses to steer by it.
    target = min(grid, key=lambda p: (p.x - 100) ** 2 + (p.y - 100) ** 2)
    # Each origin check runs wait_stable (3 captures): first check sees the
    # camera parked far from the target, the next one sees it on target.
    wrong = _rect_frame(180.0, 80.0)
    on_target = _rect_frame(target.x, target.y)
    device = FakeDevice(frames=[wrong, wrong, wrong, on_target, on_target, on_target])

    meta = _position_origin(device, cfg, target)

    assert device.taps == [(target.x, target.y)]
    assert meta["corrections"] == 1
    assert len(device.swipes) >= 1  # the residual was swiped, not re-tapped
    assert meta["landed_px"] == [
        pytest.approx(target.x, abs=1.5), pytest.approx(target.y, abs=1.5),
    ]


def test_build_scan_grid_applies_debug_window() -> None:
    full = build_scan_grid(_cfg())
    limited = build_scan_grid(_cfg(grid_limit=GridLimitConfig(cols=2, rows=3)))

    assert len(limited) == 6
    assert len(limited) < len(full)
    kept = {(p.ix, p.iy) for p in limited}
    assert kept <= {(p.ix, p.iy) for p in full}


def _white_patch_frame(x: int, y: int, r: int = 8) -> np.ndarray:
    """A clear screen with one near-white label blob centered on (x, y)."""
    frame = _grey_frame()
    frame[y - r : y + r + 1, x - r : x + r + 1] = 250
    return frame


def test_patch_is_white_flags_label_but_not_snow() -> None:
    cfg = _cfg()
    label = _white_patch_frame(300, 600)
    assert _patch_is_white(label, 300, 600, cfg) is True

    # Blue-grey snow (min channel ~200) sits under the threshold.
    snow = np.full((1280, 720, 3), (215, 205, 200), dtype=np.uint8)
    assert _patch_is_white(snow, 300, 600, cfg) is False


def test_wait_touch_clear_waits_until_label_gone() -> None:
    cfg = _cfg(
        label_guard=LabelGuardConfig(poll_interval_ms=20, timeout_ms=2000),
    )
    covered = _white_patch_frame(300, 600)
    clear = _grey_frame()
    # White for the first two polls, then the label clears.
    device = FakeDevice(frames=[covered, covered, clear])

    _wait_touch_clear(device, cfg, 300, 600)

    assert device.captures == 3  # polled until the third (clear) frame


def test_wait_touch_clear_gives_up_after_timeout() -> None:
    cfg = _cfg(label_guard=LabelGuardConfig(timeout_ms=0))
    device = FakeDevice(frames=[_white_patch_frame(300, 600)])

    _wait_touch_clear(device, cfg, 300, 600)  # never clears, but timeout=0 → touch anyway

    assert device.captures == 1


def test_wait_touch_clear_skipped_when_disabled() -> None:
    cfg = _cfg(label_guard=LabelGuardConfig(enabled=False))
    device = FakeDevice(frames=[_white_patch_frame(300, 600)])

    _wait_touch_clear(device, cfg, 300, 600)

    assert device.captures == 0  # guard off: no screenshot, no wait


def test_swipe_calibration_learns_undershoot() -> None:
    calib = SwipeCalibration()
    # Asked for 600 px, the map only moved 480 → future swipes must grow.
    calib.update((600.0, 0.0), (480.0, 0.0))
    assert calib.scale_x > 1.0
    assert calib.scale_y == 1.0  # y-component too small to judge — untouched
    fx, _fy = calib.apply(100.0, 0.0)
    assert fx > 100.0


def test_swipe_calibration_ignores_noise_and_sign_flips() -> None:
    calib = SwipeCalibration()
    calib.update((50.0, 30.0), (40.0, 20.0))      # both under min_component_px
    calib.update((600.0, 0.0), (-580.0, 0.0))     # sign mismatch: bad lock
    assert calib.scale_x == 1.0
    assert calib.scale_y == 1.0


def test_swipe_calibration_clamps_extremes() -> None:
    calib = SwipeCalibration()
    for _ in range(50):
        calib.update((600.0, 600.0), (200.0, 1900.0))  # wild ratios both ways
    assert calib.scale_x <= calib.max_scale
    assert calib.scale_y >= calib.min_scale


def test_swipe_relative_applies_calibration() -> None:
    device_raw, device_cal = FakeDevice(), FakeDevice()
    cfg = _cfg(label_guard=LabelGuardConfig(enabled=False))
    calib = SwipeCalibration(scale_y=1.5)

    raw = _swipe_relative(device_raw, cfg, 0.0, 10.0)
    scaled = _swipe_relative(device_cal, cfg, 0.0, 10.0, calib)

    raw_travel = sum(abs(s["y2"] - s["y1"]) for s in raw)
    scaled_travel = sum(abs(s["y2"] - s["y1"]) for s in scaled)
    assert scaled_travel > raw_travel


def test_bottom_anchor_without_max_frames_covers_all_rows() -> None:
    cfg = _cfg(grid_limit=GridLimitConfig(anchor="bottom"))
    full = build_scan_grid(_cfg())
    limited = build_scan_grid(cfg)
    assert {(p.ix, p.iy) for p in limited} == {(p.ix, p.iy) for p in full}


def test_scan_grid_finishes_the_row_then_stops_at_the_top_border(
    monkeypatch, tmp_path,
) -> None:
    """When the top corner enters the view, the current row is completed (full
    coverage) and the scan ends as complete — no climb into rows beyond."""
    import modules.radar.scanner as scanner_mod
    from modules.radar.geometry import Affine
    from modules.radar.scanner import _scan_grid, build_scan_walk

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(grid_limit=GridLimitConfig(anchor="bottom"))
    grid = build_scan_grid(cfg)
    walk = build_scan_walk(cfg, grid)
    assert len({p.iy for p, _ in walk}) > 1  # the route does span several rows

    # Every captured frame already shows the top-border chevron → the scan must
    # capture the entire FIRST row, then stop before entering the second.
    border_frame = _grey_frame()
    mid_x = cfg.crop.x + cfg.crop.w // 2
    band_y = cfg.crop.y + 10
    cv2.line(border_frame, (mid_x - 200, band_y + 100), (mid_x, band_y), (120, 230, 235), 4)
    cv2.line(border_frame, (mid_x + 200, band_y + 100), (mid_x, band_y), (120, 230, 235), 4)

    monkeypatch.setattr(
        scanner_mod, "_move_to_point",
        lambda *_a, **_k: {"mode": "swipe", "swipes": []},
    )
    monkeypatch.setattr(
        scanner_mod, "_guarded_capture",
        lambda *_a, **_k: (border_frame, True, None),
    )

    manifest = {"config": {}, "frames": {}}
    affine = Affine.from_corners(cfg.minimap.corners.as_geometry(), cfg.game_size)
    stopped = _scan_grid(
        scanner_mod.RadarDevice.__new__(scanner_mod.RadarDevice),
        cfg, grid, affine, manifest, tmp_path, events=None,
    )

    assert stopped is False  # complete, not user-interrupted
    captured_rows = {f["iy"] for f in manifest["frames"].values()}
    bottom_row = max(p.iy for p in grid)
    assert captured_rows == {bottom_row}
    bottom_cells = {(p.ix, p.iy) for p in grid if p.iy == bottom_row}
    captured_cells = {(f["ix"], f["iy"]) for f in manifest["frames"].values()}
    assert captured_cells == bottom_cells  # the whole row, nothing beyond
    # The top-corner crossing is recorded as the stitcher's second anchor.
    crosses = [f["top_cross_px"] for f in manifest["frames"].values() if "top_cross_px" in f]
    assert len(crosses) == 1
    assert crosses[0][0] == pytest.approx(mid_x, abs=10)
    assert crosses[0][1] == pytest.approx(band_y, abs=10)


def test_scan_grid_resume_rewalks_done_cells_and_captures_only_missing(
    monkeypatch, tmp_path,
) -> None:
    """Resuming a partial run re-walks the route — physically MOVING the camera
    through already-captured cells (not skipping them in place) so the missing
    cells are captured from a correctly tracked position."""
    import modules.radar.scanner as scanner_mod
    from modules.radar.geometry import Affine
    from modules.radar.scanner import _scan_grid, frame_filename, frame_key

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(grid_limit=GridLimitConfig(anchor="bottom"))
    grid = build_scan_grid(cfg)

    # Pre-populate the bottom row as "already captured": manifest entries + the
    # frame PNGs on disk (the resume path reloads them as registration anchors).
    bottom_row = max(p.iy for p in grid)
    done_cells = {(p.ix, p.iy) for p in grid if p.iy == bottom_row}
    manifest: dict = {"config": {}, "frames": {}}
    for p in grid:
        if (p.ix, p.iy) in done_cells:
            cv2.imwrite(str(tmp_path / frame_filename(p.ix, p.iy)), _grey_frame())
            manifest["frames"][frame_key(p.ix, p.iy)] = {
                "ix": p.ix, "iy": p.iy, "order": len(manifest["frames"]),
                "file": frame_filename(p.ix, p.iy), "unstable": False,
            }

    moved_to: list[tuple[int, int]] = []
    captures = {"n": 0}

    def fake_move(_device, _cfg, _previous, point, *_a, **_k):
        moved_to.append((point.ix, point.iy))
        return {"mode": "swipe", "swipes": []}

    def fake_capture(*_a, **_k):
        captures["n"] += 1
        return (_grey_frame(), True, None)

    monkeypatch.setattr(scanner_mod, "_move_to_point", fake_move)
    monkeypatch.setattr(scanner_mod, "_guarded_capture", fake_capture)
    affine = Affine.from_corners(cfg.minimap.corners.as_geometry(), cfg.game_size)

    stopped = _scan_grid(
        scanner_mod.RadarDevice.__new__(scanner_mod.RadarDevice),
        cfg, grid, affine, manifest, tmp_path, events=None,
    )

    assert stopped is False
    # The camera was moved THROUGH the already-done cells (the resume fix):
    # without it, done capture-cells would early-continue with no _move_to_point.
    assert done_cells.issubset(set(moved_to))
    # Only the previously-missing cells were captured — done cells not recaptured.
    assert captures["n"] == len(grid) - len(done_cells)
    # The run is now complete: every grid cell has a frame.
    assert {(f["ix"], f["iy"]) for f in manifest["frames"].values()} == {
        (p.ix, p.iy) for p in grid
    }


def test_scan_grid_complete_rerun_recaptures_nothing(monkeypatch, tmp_path) -> None:
    """Re-running a *complete* run recaptures nothing — it fast-replays the
    present frames rather than entering the resume re-walk."""
    import modules.radar.scanner as scanner_mod
    from modules.radar.geometry import Affine
    from modules.radar.scanner import _scan_grid, frame_filename, frame_key

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(grid_limit=GridLimitConfig(anchor="bottom"))
    grid = build_scan_grid(cfg)
    manifest: dict = {"config": {}, "frames": {}}
    for p in grid:
        cv2.imwrite(str(tmp_path / frame_filename(p.ix, p.iy)), _grey_frame())
        manifest["frames"][frame_key(p.ix, p.iy)] = {
            "ix": p.ix, "iy": p.iy, "order": len(manifest["frames"]),
            "file": frame_filename(p.ix, p.iy), "unstable": False,
        }

    captures = {"n": 0}
    monkeypatch.setattr(
        scanner_mod, "_move_to_point", lambda *_a, **_k: {"mode": "swipe", "swipes": []}
    )
    monkeypatch.setattr(
        scanner_mod, "_guarded_capture",
        lambda *_a, **_k: (captures.__setitem__("n", captures["n"] + 1), (_grey_frame(), True, None))[1],
    )
    affine = Affine.from_corners(cfg.minimap.corners.as_geometry(), cfg.game_size)
    _scan_grid(
        scanner_mod.RadarDevice.__new__(scanner_mod.RadarDevice),
        cfg, grid, affine, manifest, tmp_path, events=None,
    )
    assert captures["n"] == 0  # nothing recaptured on a complete re-run


def test_scan_grid_aborts_when_the_camera_lands_outside(monkeypatch, tmp_path) -> None:
    """A captured frame whose lower band is fully out-of-bounds means the
    camera crossed the border mid-walk — abort with evidence instead of
    scanning the neighbouring state."""
    import modules.radar.scanner as scanner_mod
    from modules.radar.geometry import Affine
    from modules.radar.scanner import _scan_grid

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg(grid_limit=GridLimitConfig(anchor="bottom", max_frames=15))
    grid = build_scan_grid(cfg)
    outside_frame = _grey_frame()
    outside_frame[cfg.crop.y + cfg.crop.h // 2 :, :] = (40, 42, 50)
    monkeypatch.setattr(
        scanner_mod, "_move_to_point",
        lambda *_a, **_k: {"mode": "swipe", "swipes": []},
    )
    monkeypatch.setattr(
        scanner_mod, "_guarded_capture",
        lambda *_a, **_k: (outside_frame, True, None),
    )
    manifest = {"config": {}, "frames": {}}
    affine = Affine.from_corners(cfg.minimap.corners.as_geometry(), cfg.game_size)

    with pytest.raises(ScanAborted, match="outside the kingdom"):
        _scan_grid(
            scanner_mod.RadarDevice.__new__(scanner_mod.RadarDevice),
            cfg, grid, affine, manifest, tmp_path, events=None,
        )

    assert list(tmp_path.glob("outside_*.png"))  # evidence frame saved


def test_prior_calibration_seeds_from_latest_sibling_run(tmp_path) -> None:
    """Learned swipe scales persist across runs: the newest sibling manifest
    seeds the next scan (clamped to sane bounds); broken manifests are skipped."""
    older = tmp_path / "2026-01-01_000000"
    older.mkdir()
    (older / "manifest.json").write_text(
        json.dumps({"swipe_calibration": {"scale_x": 0.7, "scale_y": 0.7}}), encoding="utf-8",
    )
    newer = tmp_path / "2026-01-02_000000"
    newer.mkdir()
    (newer / "manifest.json").write_text(
        json.dumps({"swipe_calibration": {"scale_x": 1.2, "scale_y": 5.0}}), encoding="utf-8",
    )
    broken = tmp_path / "2026-01-03_000000"
    broken.mkdir()
    (broken / "manifest.json").write_text("not json", encoding="utf-8")

    out_dir = tmp_path / "2026-01-04_000000"
    out_dir.mkdir()
    calib = _load_prior_calibration(out_dir)

    assert calib is not None
    assert calib.scale_x == pytest.approx(1.2)
    # Clamped into the trusted seed band, not the (wider) runtime band: a 5.0
    # in a manifest is an artifact (old divergent update law), not a gain.
    from modules.radar.scanner_navigation import SEED_MAX_SCALE

    assert calib.scale_y == pytest.approx(SEED_MAX_SCALE)


def test_swipe_calibration_converges_under_constant_gain() -> None:
    """Closed-loop sanity: with the game eating a constant share of every
    swipe, the learned scale settles at 1/gain instead of growing without
    bound (feeding back the commanded-finger ratio diverged geometrically —
    one real scan inflated scale_x to 1.53 and tore a whole row apart)."""
    gain = 0.95
    calib = SwipeCalibration()
    for _ in range(40):
        desired = 360.0
        finger, _fy = calib.apply(desired, 0.0)
        measured = finger * gain
        calib.update((desired, 0.0), (measured, 0.0))
    assert calib.scale_x == pytest.approx(1.0 / gain, abs=0.02)


def test_prior_calibration_none_without_history(tmp_path) -> None:
    out_dir = tmp_path / "2026-01-01_000000"
    out_dir.mkdir()
    assert _load_prior_calibration(out_dir) is None


# ---------------------------------------------------------------------------
# Directional border guard: yellow ahead is a crossing only when the dark
# out-of-bounds mass lies on the path too (the corner X's arms span the whole
# view, so a yellow-only test used to block moves INTO the kingdom).
# ---------------------------------------------------------------------------


def _border_with_gap_below(cfg: RadarConfig) -> np.ndarray:
    """Dashed yellow line through the crop center column + dark gap at the bottom."""
    frame = _grey_frame()
    c = cfg.crop
    cx = c.x + c.w // 2
    # Vertical dashed yellow line crossing the center: yellow lies ahead for
    # BOTH up and down moves — only the dark mass disambiguates them.
    for y in range(c.y, c.y + c.h, 16):
        cv2.line(frame, (cx, y), (cx, y + 8), (120, 230, 235), 4)
    # Dark inter-kingdom gap along the crop bottom (flood-fill reaches it).
    frame[c.y + c.h - 220 : c.y + c.h, :] = (40, 42, 50)
    return frame


def test_border_swipe_guard_blocks_only_toward_the_dark_gap() -> None:
    cfg = _cfg()
    frame = _border_with_gap_below(cfg)

    # Downward, toward the gap: yellow ahead AND dark ahead — blocked.
    _dx, dy, meta = _border_swipe_guard(cfg, frame, 0.0, cfg.viewport.rect_h)
    assert meta is not None
    assert dy < cfg.viewport.rect_h

    # Upward, into the kingdom: the same yellow is ahead, but the dark mass
    # is behind — a flank, not a crossing; the move passes unshortened.
    assert _border_swipe_guard(cfg, frame, 0.0, -cfg.viewport.rect_h) == (
        0.0, -cfg.viewport.rect_h, None,
    )


def test_border_swipe_guard_ignores_decor_yellow_without_the_gap() -> None:
    """Yellow ahead without any out-of-bounds mass anywhere is decor (pale
    trails, icon rows), not the border — a real border that close always
    shows some of the gap behind it. One such line once pinned a whole scan."""
    cfg = _cfg()
    frame = _grey_frame()
    c = cfg.crop
    cx, cy = c.x + c.w // 2, c.y + c.h // 2
    # A fitted-quality sloped line crossing the rightward corridor — but no gap.
    cv2.line(frame, (cx + 120, cy - 60), (cx + 280, cy + 60), (120, 230, 235), 4)
    assert _border_swipe_guard(cfg, frame, cfg.viewport.rect_w, 0.0) == (
        cfg.viewport.rect_w, 0.0, None,
    )


def test_border_swipe_guard_ignores_tinted_dark_sprites() -> None:
    """Dark TINTED content (mountains, beast sprites at the crop edge) is not
    the inter-kingdom gap: it must neither fake 'outside ahead' nor block.
    Reproduces the run where edge sprites + golden icons pinned the camera."""
    cfg = _cfg()
    frame = _grey_frame()
    c = cfg.crop
    cx, cy = c.x + c.w // 2, c.y + c.h // 2
    # Dark navy blobs touching the left crop edge (tinted: spread ~95).
    cv2.rectangle(frame, (c.x, cy - 70), (c.x + 90, cy + 70), (130, 60, 35), -1)
    # Golden icon noise in the leftward corridor near the center.
    cv2.circle(frame, (cx - 60, cy + 10), 9, (60, 200, 230), -1)
    assert _border_swipe_guard(cfg, frame, -cfg.viewport.rect_w, 0.0) == (
        -cfg.viewport.rect_w, 0.0, None,
    )


# ---------------------------------------------------------------------------
# Honest manifest across guarded moves: a cell the camera never reached is
# skipped (recorded in skipped_cells), not captured as a duplicate; the
# unachieved travel carries into the next move.
# ---------------------------------------------------------------------------


def test_scan_grid_skips_unreachable_cell_and_carries_shortfall(
    monkeypatch, tmp_path,
) -> None:
    import modules.radar.scanner as scanner_mod
    from modules.radar.geometry import Affine
    from modules.radar.scanner import _scan_grid

    monkeypatch.setattr(scanner_mod.time, "sleep", lambda _s: None)
    cfg = _cfg()
    grid = build_scan_grid(cfg)
    affine = Affine.from_corners(cfg.minimap.corners.as_geometry(), cfg.game_size)

    monkeypatch.setattr(
        scanner_mod, "_position_origin",
        lambda *_a, **_k: {"mode": "tap", "origin": True},
    )
    monkeypatch.setattr(
        scanner_mod, "_guarded_capture",
        lambda *_a, **_k: (_grey_frame(), True, None),
    )
    # Zero out the SECOND swipe move (the walk's third cell); allow the rest.
    calls = {"n": 0}

    def fake_guard(_cfg, _frame, dx, dy):
        calls["n"] += 1
        if calls["n"] == 2:
            return 0.0, 0.0, {"travel_scale": 0.0}
        return dx, dy, None

    monkeypatch.setattr(scanner_mod, "_border_swipe_guard", fake_guard)

    manifest = {"config": {}, "frames": {}}
    device = FakeDevice()
    _scan_grid(device, cfg, grid[:4], affine, manifest, tmp_path, events=None)

    blocked = grid[2]
    after = grid[3]
    blocked_key = f"{blocked.ix:02d}_{blocked.iy:02d}"
    after_key = f"{after.ix:02d}_{after.iy:02d}"

    # The unreached cell produced no frame and is recorded as skipped.
    assert blocked_key not in manifest["frames"]
    assert manifest["skipped_cells"] == [
        {
            "ix": blocked.ix,
            "iy": blocked.iy,
            "shortfall_minimap_px": [
                round(blocked.x - grid[1].x, 2),
                round(blocked.y - grid[1].y, 2),
            ],
        },
    ]
    # The next move carried the shortfall: its commanded delta covers BOTH steps.
    entry_after = manifest["frames"][after_key]
    assert entry_after["move"]["delta_minimap_px"] == [
        pytest.approx(after.x - grid[1].x, abs=0.05),
        pytest.approx(after.y - grid[1].y, abs=0.05),
    ]
    # And the carry is repaid — nothing left over.
    assert entry_after["move"]["shortfall_minimap_px"] == [0.0, 0.0]
