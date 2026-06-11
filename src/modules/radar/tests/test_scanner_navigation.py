"""Swipe navigation math for radar scans."""

from modules.radar.config import (
    CornersConfig,
    CropConfig,
    MinimapConfig,
    NavigationConfig,
    RadarConfig,
    StitchViewportConfig,
    ViewportConfig,
)
from modules.radar.scanner import _swipe_relative


class FakeDevice:
    def __init__(self) -> None:
        self.swipes: list[tuple[float, float, float, float, int]] = []

    def swipe(self, x1: float, y1: float, x2: float, y2: float, duration_ms: int) -> None:
        self.swipes.append((x1, y1, x2, y2, duration_ms))


def _cfg() -> RadarConfig:
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
        navigation=NavigationConfig(
            mode="swipe",
            swipe_duration_ms=450,
            swipe_margin_px=48,
            swipe_scale=1.0,
        ),
    )


def test_swipe_relative_inverts_camera_delta_and_stays_inside_crop() -> None:
    device = FakeDevice()

    emitted = _swipe_relative(device, _cfg(), minimap_dx=15.6, minimap_dy=25.35)

    assert emitted == [
        {"x1": 572, "y1": 1048, "x2": 104, "y2": 278, "ms": 450},
    ]
    assert device.swipes == [(572, 1048, 104, 278, 450)]
