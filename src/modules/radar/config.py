"""Radar configuration: pydantic models + YAML load/save (``radar_config.yaml``).

Every runtime number — timings and geometry — lives here so the scanner and
stitcher contain no magic constants.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from modules.radar.geometry import Corners

CONFIG_VERSION = 1
RUNS_DIR_ENV = "RADAR_RUNS_DIR"
DEFAULT_CONFIG_NAME = "radar_config.yaml"
MINIMAP_REFERENCE_NAME = "radar_minimap_ref.png"

# Everything radar needs lives inside the module: config + calibration assets.
_MODULE_DIR = Path(__file__).resolve().parent


def default_config_path() -> Path:
    """``src/modules/radar/radar_config.yaml`` — the single config location."""
    return _MODULE_DIR / DEFAULT_CONFIG_NAME


def minimap_reference_path(name: str = MINIMAP_REFERENCE_NAME) -> Path:
    """Calibration reference image, resolved next to the config."""
    return _MODULE_DIR / name


def runs_root() -> Path:
    """Directory holding all scan runs — ``RADAR_RUNS_DIR`` or ``<repo>/runs``."""
    raw = os.environ.get(RUNS_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from config.paths import repo_root

    return repo_root() / "runs"


class CornersConfig(BaseModel):
    """Diamond corners in absolute screen pixels."""

    top: tuple[float, float]
    right: tuple[float, float]
    bottom: tuple[float, float]
    left: tuple[float, float]

    def as_geometry(self) -> Corners:
        return Corners(top=self.top, right=self.right, bottom=self.bottom, left=self.left)


class MinimapConfig(BaseModel):
    """Where the minimap sits on screen."""

    bbox: tuple[int, int, int, int]  # x, y, w, h
    corners: CornersConfig
    reference: str = MINIMAP_REFERENCE_NAME


class ViewportConfig(BaseModel):
    """Camera viewport size inside the minimap, used to derive tap spacing."""

    rect_w: int = Field(gt=0)
    rect_h: int = Field(gt=0)


class CropConfig(BaseModel):
    """Useful game area on the main screen (HUD/chat/nav excluded).

    Saved frames stay full screenshots (one coordinate system), but this rect
    bounds everything downstream: swipe gestures, the stabilization region,
    ORB feature detection AND what gets pasted onto the stitched canvas — so
    the chat, bottom nav and side buttons never reach the map.
    """

    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(gt=0)
    h: int = Field(gt=0)


class StitchViewportConfig(BaseModel):
    """Visible map viewport size used to place cropped frames on the canvas."""

    w: int = Field(gt=0)
    h: int = Field(gt=0)


class GridLimitConfig(BaseModel):
    """Debug window: scan only ``cols×rows`` cells around the diamond center."""

    cols: int = Field(gt=0)
    rows: int = Field(gt=0)


class NavigationConfig(BaseModel):
    """How the scanner moves the map between frames.

    ``swipe`` is the default: minimap tap-teleports proved imprecise (the
    game clamps/quantizes the jump), while swipe drift does not matter —
    the stitcher measures real offsets from ORB features afterwards.
    """

    mode: Literal["swipe", "tap"] = "swipe"
    swipe_duration_ms: int = Field(default=450, ge=100, le=2000)
    swipe_margin_px: int = Field(default=48, ge=0)
    swipe_scale: float = Field(default=1.0, gt=0.0, le=2.0)


class TimingsConfig(BaseModel):
    """Waits and stabilization thresholds used by the scan loop."""

    post_tap_delay_ms: int = Field(default=300, ge=0)
    stabilize_interval_ms: int = Field(default=150, ge=10)
    stabilize_diff_threshold: float = Field(default=2.0, gt=0)
    stabilize_consecutive: int = Field(default=2, ge=1)
    stabilize_timeout_ms: int = Field(default=5000, ge=100)


class RadarConfig(BaseModel):
    """Everything ``radar scan`` needs."""

    version: int = CONFIG_VERSION
    device_serial: str = ""
    adb_bin: str = "adb"
    minimap: MinimapConfig
    viewport: ViewportConfig
    # Fraction of the viewport shared between neighbouring frames. With swipe
    # navigation high overlap is *good*: a half-screen step (0.5) guarantees
    # >50% common content per pair, which is what makes ORB registration
    # reliable. (Tap mode caveat: jumps shorter than ~half a viewport may not
    # move the camera at all — keep overlap below 0.5 if you switch to taps.)
    overlap: float = Field(default=0.5, ge=0.0, le=0.75)
    edge_margin_px: float | None = Field(default=None, ge=0.0)
    crop: CropConfig
    stitch_viewport: StitchViewportConfig | None = None
    # When set, scan only this many grid cells around the center (debug runs).
    grid_limit: GridLimitConfig | None = None
    game_size: int = Field(default=1200, gt=1)
    navigation: NavigationConfig = NavigationConfig()
    timings: TimingsConfig = TimingsConfig()


def load_config(path: Path) -> RadarConfig:
    if not path.is_file():
        msg = f"radar config not found: {path} — create src/modules/radar/radar_config.yaml first"
        raise FileNotFoundError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"radar config {path} is not a YAML mapping"
        raise TypeError(msg)
    cfg = RadarConfig.model_validate(raw)
    if cfg.version != CONFIG_VERSION:
        msg = (
            f"radar config {path} has version {cfg.version}, expected {CONFIG_VERSION} — "
            "refresh radar_config.yaml"
        )
        raise ValueError(msg)
    return cfg


def save_config(cfg: RadarConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mode="json" turns tuples into lists, which is what yaml.safe_dump can emit.
    path.write_text(
        yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
