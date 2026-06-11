"""Scan navigation: swipe math + route building (swipe default, tap optional)."""

import json
import math
from itertools import pairwise

import cv2
import numpy as np
import pytest

from modules.radar.config import (
    BorderConfig,
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
    _viewport_rect_center,
    _wait_touch_clear,
    build_scan_grid,
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
    """Grey screen with a dashed yellow border line ``offset_px`` right of the crop center."""
    frame = _grey_frame()
    cx = cfg.crop.x + cfg.crop.w // 2
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
    assert 0.0 <= travel <= 200 - cfg.border.cross_margin_px + 5
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
        border=BorderConfig(require_cross=False, max_blind_screens=4.0),
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
    target = grid[0]
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
    assert calib.scale_y == pytest.approx(calib.max_scale)  # clamped from 5.0


def test_prior_calibration_none_without_history(tmp_path) -> None:
    out_dir = tmp_path / "2026-01-01_000000"
    out_dir.mkdir()
    assert _load_prior_calibration(out_dir) is None
