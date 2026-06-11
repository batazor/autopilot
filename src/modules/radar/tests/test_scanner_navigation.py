"""Scan navigation: swipe math + route building (swipe default, tap optional)."""

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
from modules.radar.geometry import diamond_center
from modules.radar.scanner import _swipe_relative, build_scan_grid


class FakeDevice:
    def __init__(self) -> None:
        self.swipes: list[tuple[float, float, float, float, int]] = []

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None:
        self.swipes.append((x1, y1, x2, y2, duration_ms))


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


def test_swipe_relative_chunks_long_moves() -> None:
    device = FakeDevice()

    emitted = _swipe_relative(device, _cfg(), minimap_dx=60.0, minimap_dy=0.0)

    assert len(emitted) > 1  # finger travel > crop width → split into chunks
    assert len(device.swipes) == len(emitted)
    c = _cfg().crop
    for x1, y1, x2, y2, _ms in device.swipes:
        for x in (x1, x2):
            assert c.x <= x <= c.x + c.w
        for y in (y1, y2):
            assert c.y <= y <= c.y + c.h


def test_build_scan_grid_swipe_route_starts_near_center() -> None:
    cfg = _cfg()  # swipe is the default mode
    grid = build_scan_grid(cfg)

    assert grid == build_scan_grid(cfg)  # deterministic
    cx, cy = diamond_center(cfg.minimap.corners.as_geometry())
    start = grid[0]
    # The route begins at the cell closest to the minimap center (where the
    # camera already is), so the first relative move is short.
    assert min(
        ((p.x - cx) ** 2 + (p.y - cy) ** 2) for p in grid
    ) == (start.x - cx) ** 2 + (start.y - cy) ** 2


def test_build_scan_grid_tap_mode_is_serpentine() -> None:
    grid = build_scan_grid(_cfg(navigation=NavigationConfig(mode="tap")))

    rows: dict[int, list[int]] = {}
    for p in grid:
        rows.setdefault(p.iy, []).append(p.ix)
    for iy, ixs in rows.items():
        assert ixs == sorted(ixs, reverse=iy % 2 == 1)  # serpentine raster


def test_build_scan_grid_applies_debug_window() -> None:
    full = build_scan_grid(_cfg())
    limited = build_scan_grid(_cfg(grid_limit=GridLimitConfig(cols=2, rows=3)))

    assert len(limited) == 6
    assert len(limited) < len(full)
    kept = {(p.ix, p.iy) for p in limited}
    assert kept <= {(p.ix, p.iy) for p in full}
