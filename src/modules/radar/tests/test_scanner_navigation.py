"""Tap-only navigation: the scan route is a deterministic minimap tap grid."""

from modules.radar.config import (
    CornersConfig,
    CropConfig,
    GridLimitConfig,
    MinimapConfig,
    RadarConfig,
    StitchViewportConfig,
    ViewportConfig,
)
from modules.radar.scanner import build_scan_grid


def _cfg(grid_limit: GridLimitConfig | None = None) -> RadarConfig:
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
    )


def test_build_scan_grid_is_deterministic_serpentine() -> None:
    grid = build_scan_grid(_cfg())

    assert grid == build_scan_grid(_cfg())  # same config → identical tap targets
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
