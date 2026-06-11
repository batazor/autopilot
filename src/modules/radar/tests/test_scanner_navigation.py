"""Scan navigation: swipe math + route building (swipe default, tap optional)."""

from itertools import pairwise

from modules.radar.config import (
    CornersConfig,
    CropConfig,
    GridLimitConfig,
    MinimapConfig,
    NavigationConfig,
    RadarConfig,
    StitchViewportConfig,
    ViewportConfig,
)
from modules.radar.scanner import _move_to_point, _swipe_relative, build_scan_grid


class FakeDevice:
    def __init__(self) -> None:
        self.swipes: list[tuple[float, float, float, float, int]] = []
        self.taps: list[tuple[float, float]] = []

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None:
        self.swipes.append((x1, y1, x2, y2, duration_ms))

    def tap(self, x: float, y: float) -> None:
        self.taps.append((x, y))


def _cfg(
    grid_limit: GridLimitConfig | None = None,
    navigation: NavigationConfig | None = None,
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
    )


def test_swipe_relative_inverts_camera_delta_and_stays_inside_crop() -> None:
    device = FakeDevice()

    emitted = _swipe_relative(device, _cfg(), minimap_dx=15.6, minimap_dy=25.35)

    assert emitted == [
        {"x1": 572, "y1": 1048, "x2": 104, "y2": 278, "ms": 450},
    ]
    assert device.swipes == [(572, 1048, 104, 278, 450)]


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


def test_first_swipe_move_positions_from_minimap_center() -> None:
    device = FakeDevice()
    cfg = _cfg()
    grid = build_scan_grid(cfg)

    meta = _move_to_point(device, cfg, None, grid[0])

    # The pre-capture positioning move runs from the diamond center (100,100)
    # to the first raster cell — long is fine, no frame exists yet.
    assert meta["mode"] == "swipe"
    assert meta["origin"] is True
    assert meta["from_center_px"] == [100.0, 100.0]
    expected_moves = (grid[0].x != 100.0) or (grid[0].y != 100.0)
    assert bool(device.swipes) is expected_moves
    assert bool(meta["swipes"]) is expected_moves


def test_build_scan_grid_applies_debug_window() -> None:
    full = build_scan_grid(_cfg())
    limited = build_scan_grid(_cfg(grid_limit=GridLimitConfig(cols=2, rows=3)))

    assert len(limited) == 6
    assert len(limited) < len(full)
    kept = {(p.ix, p.iy) for p in limited}
    assert kept <= {(p.ix, p.iy) for p in full}
