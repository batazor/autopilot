"""Radar configuration: pydantic models + YAML load/save (``radar_config.yaml``).

Every runtime number — timings and geometry — lives here so the scanner and
stitcher contain no magic constants.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

from modules.radar.geometry import Corners

CONFIG_VERSION = 1
RUNS_DIR_ENV = "RADAR_RUNS_DIR"
# Runtime config + calibration directory. In production the code layer may be
# read-only (or simply awkward to update in place), so the operator points
# ``RADAR_DATA_DIR`` at a writable location and the config, minimap reference
# and corner sidecar are resolved/written there. Unset → the in-module assets
# are used exactly as before (fully backward compatible).
DATA_DIR_ENV = "RADAR_DATA_DIR"
DEFAULT_CONFIG_NAME = "radar_config.yaml"
MINIMAP_REFERENCE_NAME = "radar_minimap_ref.png"
# Sidecar for the recorded corner reference: written by the calibration
# endpoint, so the hand-commented main YAML is never rewritten by code.
CORNER_REF_NAME = "radar_corner_ref.yaml"

# Scan targets — three independent game views the radar can map. Each owns its
# own config file and corner-reference sidecar (geometry differs per view), but
# the capture/stitch engine is target-agnostic. ``global_map`` keeps the bare
# ``radar_config.yaml`` / ``radar_corner_ref.yaml`` names for backward
# compatibility; the others get a ``_<target>`` suffix.
DEFAULT_TARGET = "global_map"
RADAR_TARGETS: tuple[str, ...] = ("global_map", "main_city", "island")

# Fallback home for config + calibration assets when RADAR_DATA_DIR is unset.
_MODULE_DIR = Path(__file__).resolve().parent


def normalize_target(raw: str | None) -> str:
    """Validate a scan-target string, defaulting blank/None to ``global_map``."""
    target = (raw or "").strip() or DEFAULT_TARGET
    if target not in RADAR_TARGETS:
        msg = f"unknown radar target {target!r}; expected one of {', '.join(RADAR_TARGETS)}"
        raise ValueError(msg)
    return target


def _suffixed(name: str, target: str) -> str:
    """``radar_config.yaml`` for the default target, ``radar_config_<target>.yaml`` otherwise."""
    if target == DEFAULT_TARGET:
        return name
    stem, _, ext = name.rpartition(".")
    return f"{stem}_{target}.{ext}" if ext else f"{name}_{target}"


def config_name_for(target: str = DEFAULT_TARGET) -> str:
    return _suffixed(DEFAULT_CONFIG_NAME, normalize_target(target))


def corner_ref_name_for(target: str = DEFAULT_TARGET) -> str:
    return _suffixed(CORNER_REF_NAME, normalize_target(target))


def data_dir() -> Path | None:
    """Writable runtime dir for config/calibration, or ``None`` when unset."""
    raw = os.environ.get(DATA_DIR_ENV, "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _resolve_read(name: str) -> Path:
    """Prefer the data-dir copy when it exists, else the in-module asset.

    Reads tolerate a missing data-dir file (a partially provisioned data dir
    still falls back to the committed asset); writes always target the data
    dir when one is configured — see :func:`_resolve_write`.
    """
    base = data_dir()
    if base is not None and (base / name).exists():
        return base / name
    return _MODULE_DIR / name


def _resolve_write(name: str) -> Path:
    """Where code writes a runtime asset: the data dir if set, else the module."""
    base = data_dir()
    if base is not None:
        base.mkdir(parents=True, exist_ok=True)
        return base / name
    return _MODULE_DIR / name


def default_config_path(target: str = DEFAULT_TARGET) -> Path:
    """Radar config path for ``target`` — ``$RADAR_DATA_DIR`` copy if present, else in-module."""
    return _resolve_read(config_name_for(target))


def minimap_reference_path(name: str = MINIMAP_REFERENCE_NAME) -> Path:
    """Calibration reference image, resolved next to the config."""
    return _resolve_read(name)


def corner_ref_path(target: str = DEFAULT_TARGET) -> Path:
    """Recorded corner reference (sidecar) for ``target``, resolved next to the config."""
    return _resolve_read(corner_ref_name_for(target))


def runs_root() -> Path:
    """Directory holding all scan runs — ``RADAR_RUNS_DIR`` or ``<repo>/runs``."""
    raw = os.environ.get(RUNS_DIR_ENV, "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    from config.paths import repo_root

    return repo_root() / "runs"


# Subdirectory under the runs root that holds per-account scan trees.
ACCOUNTS_DIRNAME = "accounts"


def account_runs_root(account: str | None = None) -> Path:
    """Per-account scan root: ``<runs>/accounts/<account>`` when ``account`` is set,
    else the global :func:`runs_root`.

    A city's building layout is per-account (every Chief's city differs), but the
    runs tree was a single global directory — so a ``main_city`` scan taken on one
    account got reused for another, and ``navigate_to_building`` either routed
    against the wrong city or returned ``not_in_map``. Scoping the citymap by the
    active account fixes that: each account keeps its own ``citymap`` + scan runs
    under ``<runs>/accounts/<account>``. A blank/``None`` account falls back to the
    global root, so untargeted callers (the operator's global-map scans, run-by-id
    lookups, deletes) keep working exactly as before.
    """
    acc = str(account or "").strip()
    if not acc:
        return runs_root()
    return runs_root() / ACCOUNTS_DIRNAME / acc


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
    """Debug window: scan a subset of cells instead of the whole kingdom.

    - ``anchor: center`` (default) — a ``cols×rows`` block around the diamond
      center; scans where the camera already is.
    - ``anchor: bottom`` — start at the bottom corner and walk upward, keeping
      the first ``max_frames`` cells (``cols``/``rows`` ignored). Used to grow
      the scan window gradually from the bottom of the map. With ``max_frames``
      omitted the route covers every row — the scan then ends on its own when
      the top border enters the view (``border.stop_at_top``).
    """

    cols: int | None = Field(default=None, gt=0)
    rows: int | None = Field(default=None, gt=0)
    anchor: Literal["center", "bottom"] = "center"
    max_frames: int | None = Field(default=None, gt=0)
    # anchor=bottom only: drop this many of the lowest rows so the wedge starts
    # higher than the bare vertex (which sits on the kingdom edge).
    bottom_skip_rows: int = Field(default=0, ge=0)
    # anchor=bottom only: add this many capture rows BELOW the diamond-fitted
    # raster, clamped near the minimap's bottom vertex — the fitted raster is
    # centered on the diamond, so its lowest row can sit well above the tip.
    bottom_overscan_rows: int = Field(default=0, ge=0)
    # How far above the bare vertex the overscan rows stop (minimap px). A tap
    # on the tip itself teleports into the neighbouring state — stay inside.
    bottom_overscan_inset_px: float = Field(default=5.0, ge=0.0)
    # anchor=bottom + border.servo OFF only: after the origin tap converges on
    # the start cell, pan the camera this many screen-heights further down —
    # with swipes, which cross the kingdom border freely (a tap below the
    # vertex cannot). With the servo on this blind pan is skipped: the servo
    # approaches the corner in measured steps that stop on the visible line.
    bottom_descend_screens: float = Field(default=0.0, ge=0.0, le=3.0)

    @model_validator(mode="after")
    def _check_anchor_fields(self) -> GridLimitConfig:
        if self.anchor == "center" and (self.cols is None or self.rows is None):
            msg = "grid_limit anchor 'center' requires cols and rows"
            raise ValueError(msg)
        return self


class RasterConfig(BaseModel):
    """Fixed screen-space raster for views WITHOUT a minimap world grid.

    The world map derives its scan route from the minimap diamond corners; the
    city interior and event islands have no such minimap, so the route is a
    plain ``cols × rows`` raster centered on the start view, walked by swipes of
    explicit screen-px steps. The stitcher reconstructs true frame positions
    from measured ORB offsets, so these steps only need to guarantee that
    neighbouring frames overlap (step ≈ crop dimension × (1 − overlap)). When
    set, this REPLACES the diamond grid and any ``grid_limit``; pair it with
    ``border.servo: false`` and a tight ``crop`` over the map area only.
    """

    cols: int = Field(gt=0)
    rows: int = Field(gt=0)
    step_x_px: float = Field(gt=0)
    step_y_px: float = Field(gt=0)


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
    # Learn the real px-moved-per-px-swiped ratio from measured ORB offsets and
    # auto-correct subsequent swipes (chronic over/undershoot between rows).
    swipe_autoscale: bool = True
    # Pause between chunked swipes of one long move. Two quick touches read
    # as the double-tap-drag ZOOM gesture in-game — this gap prevents it.
    chunk_pause_ms: int = Field(default=500, ge=0, le=5000)


class BorderConfig(BaseModel):
    """Steering and stopping against the visible yellow kingdom border.

    The minimap teleport is quantized and untrusted, so the origin closes the
    loop on what the camera actually sees: the V where the two border lines
    converge (the map's bottom corner). The same detector ends an unbounded
    scan when the *top* corner enters the view.
    """

    servo: bool = True
    # Where the line crossing should sit in the frame: fraction of the crop
    # height (horizontally it is steered to the crop center).
    target_frac: float = Field(default=0.66, gt=0.0, lt=1.0)
    tolerance_px: int = Field(default=80, ge=10)
    # Generous: the approach may need ~10 blind descend steps (max_blind_screens
    # / approach_step_screens) AND a few corrections before the crossing locks.
    max_steps: int = Field(default=16, ge=1)
    # Capture starts only once the X where the two dashed lines cross (the
    # kingdom corner) is actually in view: if the servo exhausts max_steps
    # without ever seeing it, the scan aborts instead of shooting blind —
    # a single side line sweeping the frame must not fake an origin lock.
    require_cross: bool = True
    # Blind descend step (screen-heights) while the border is not visible yet.
    # Kept gentle: a big leap can jump past the kingdom's bottom vertex (the
    # corner) straight into the neighbouring state before any border shows.
    approach_step_screens: float = Field(default=0.3, gt=0.0, le=2.0)
    # Hard cap on the TOTAL blind descend (screen-heights) before the border
    # is ever seen. The tap point sits ~1.5 screens above the corner; the
    # margin covers minimap-scale uncertainty (see safe_tap_inset_px). A
    # descend past the cap means the start is off — stop instead of marching.
    max_blind_screens: float = Field(default=3.0, gt=0.0, le=6.0)
    # Lower-band out-of-bounds fraction above which the camera is judged to be
    # IN the inter-kingdom gap (across the border): the servo then climbs back
    # toward the kingdom instead of descending. Set above a valid edge frame
    # (~0.85) and below the all-dark gap (~1.0). The robust anti-cross stop.
    gap_back_off_frac: float = Field(default=0.9, gt=0.0, le=1.0)
    # Origin tap target: this many minimap px above the bottom vertex, toward
    # the diamond center. IMPORTANT SCALE FACT: the white minimap "rect" is a
    # fixed-size PIN graphic (~24x39), NOT the viewport extent — the true
    # world scale is only ~4 minimap px per screen of camera travel. A
    # fraction-of-diamond tap (the old 25% ≈ 17 px) therefore landed 4+
    # screens above the corner, beyond any sane blind budget. ~6 px ≈ 1.5
    # screens above the vertex: close enough for the servo, safely inside.
    safe_tap_inset_px: float = Field(default=6.0, ge=0.0, le=40.0)
    # End an unbounded bottom-up scan when the top corner enters the view.
    stop_at_top: bool = True
    # Don't carry the camera across the border with inter-cell swipes: before
    # each move the last captured frame is probed for the yellow line along
    # the motion path, and a move reaching past it is shortened to stop
    # ``cross_margin_px`` short of the line.
    block_crossing: bool = True
    cross_margin_px: int = Field(default=140, ge=0)
    # Half-width of the look-ahead corridor around the motion axis (px).
    cross_corridor_px: int = Field(default=80, ge=10)


class CornerRefConfig(BaseModel):
    """Measured reference of the view AT the bottom corner.

    Captured once from a manually positioned camera with the corner crossing
    on screen (dashboard "Calibrate corner" / ``POST /api/radar/corner-ref``).
    Converts "what does the corner look like and where does the minimap rect
    sit there" from a guess into a recorded fact the servo verifies against —
    including the rect reading, which near the bottom is display-clamped and
    only comparable against a reference taken at the same spot.
    """

    # The dashed-line crossing position in frame px at the reference view.
    cross_px: tuple[float, float]
    # Minimap viewport-rect center reading at the corner (display-clamped).
    rect_px: tuple[float, float] | None = None
    rect_size: tuple[int, int] | None = None
    # Lower-band out-of-bounds fraction at the corner view.
    outside_lower: float = 0.0


class LabelGuardConfig(BaseModel):
    """Wait out white UI labels sitting on the next touch point.

    Player/marker labels and minimap overlays are near-white; a touch landing
    on one selects the label instead of panning/teleporting. Before each move
    the scanner samples the touch point and, if it is mostly white, waits for
    the label to clear (they are transient) before touching down.
    """

    enabled: bool = True
    # A pixel counts as "label" when every BGR channel is at least this bright.
    # Snow is blue-grey (min channel ~200), well under this; label white is ~250.
    white_threshold: int = Field(default=235, ge=0, le=255)
    # Patch counts as covered when at least this fraction of it is label-white.
    white_fraction: float = Field(default=0.5, gt=0.0, le=1.0)
    # Half-size of the square sampled around the touch point.
    sample_radius_px: int = Field(default=6, ge=1)
    # Give up waiting after this long and touch anyway (a clear is not guaranteed).
    timeout_ms: int = Field(default=4000, ge=0)
    poll_interval_ms: int = Field(default=250, ge=20)


class TimingsConfig(BaseModel):
    """Waits and stabilization thresholds used by the scan loop."""

    post_tap_delay_ms: int = Field(default=300, ge=0)
    stabilize_interval_ms: int = Field(default=150, ge=10)
    stabilize_diff_threshold: float = Field(default=2.0, gt=0)
    stabilize_consecutive: int = Field(default=2, ge=1)
    stabilize_timeout_ms: int = Field(default=5000, ge=100)
    # View guard: a captured frame must ORB-register against the previous
    # one (same zoom, pure pan). On mismatch the scanner waits and
    # recaptures up to ``zoom_retry_count`` times, then aborts the scan.
    zoom_retry_delay_ms: int = Field(default=1000, ge=0)
    zoom_retry_count: int = Field(default=2, ge=0)


class RadarConfig(BaseModel):
    """Everything ``radar scan`` needs."""

    version: int = CONFIG_VERSION
    # Which game view this config maps (global_map / main_city / island). Set by
    # ``load_config`` from the requested target so it is recorded in the run
    # manifest; the YAML itself need not carry it.
    target: str = DEFAULT_TARGET
    device_serial: str = ""
    adb_bin: str = "adb"
    minimap: MinimapConfig
    viewport: ViewportConfig
    # Fraction of the viewport shared between neighbouring frames. With swipe
    # navigation high overlap is *good*: it densifies coverage (smaller steps →
    # finer edge staircase, fewer perimeter gaps) and strengthens ORB matching;
    # the cost is more frames (~1/(1-overlap)^2). 0.77 ≈ 250 cells vs 55 at 0.5.
    # (Tap mode caveat: jumps shorter than ~half a viewport may not move the
    # camera at all — keep overlap below 0.5 if you switch to taps.)
    overlap: float = Field(default=0.5, ge=0.0, le=0.85)
    edge_margin_px: float | None = Field(default=None, ge=0.0)
    crop: CropConfig
    stitch_viewport: StitchViewportConfig | None = None
    # When set, scan only this many grid cells around the center (debug runs).
    grid_limit: GridLimitConfig | None = None
    # When set, the scan route is a plain screen-space raster (no minimap
    # diamond) — for the city interior / islands. Replaces the diamond grid and
    # grid_limit; see RasterConfig.
    raster: RasterConfig | None = None
    game_size: int = Field(default=1200, gt=1)
    # Multiply the scan route's reach about the diamond center. The minimap
    # diamond was calibrated against a central sub-region (~1/9 of the true
    # kingdom — measured ×3.0 short per axis on bs1), so the bottom-anchored
    # grid climbs only ~a third of the way up. ``grid_scale`` enlarges the grid
    # extent + move-prior scale to span the real kingdom; origin positioning and
    # the live minimap rect keep the unscaled diamond. 1.0 = legacy behaviour.
    grid_scale: float = Field(default=1.0, ge=1.0, le=6.0)
    navigation: NavigationConfig = NavigationConfig()
    timings: TimingsConfig = TimingsConfig()
    label_guard: LabelGuardConfig = LabelGuardConfig()
    border: BorderConfig = BorderConfig()
    # Loaded from the sidecar (corner_ref_path()), not from the main YAML.
    corner_ref: CornerRefConfig | None = None


def load_config(path: Path, target: str = DEFAULT_TARGET) -> RadarConfig:
    target = normalize_target(target)
    if not path.is_file():
        msg = f"radar config not found: {path} — create {config_name_for(target)} first"
        raise FileNotFoundError(msg)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        msg = f"radar config {path} is not a YAML mapping"
        raise TypeError(msg)
    cfg = RadarConfig.model_validate(raw)
    cfg.target = target
    if cfg.version != CONFIG_VERSION:
        msg = (
            f"radar config {path} has version {cfg.version}, expected {CONFIG_VERSION} — "
            f"refresh {config_name_for(target)}"
        )
        raise ValueError(msg)
    if cfg.corner_ref is None:
        sidecar = corner_ref_path(target)
        if sidecar.is_file():
            cfg.corner_ref = CornerRefConfig.model_validate(
                yaml.safe_load(sidecar.read_text(encoding="utf-8")),
            )
    return cfg


def save_corner_ref(
    ref: CornerRefConfig,
    target: str = DEFAULT_TARGET,
    path: Path | None = None,
) -> Path:
    """Persist the corner reference for ``target`` to its sidecar (main YAML stays as-is).

    Without an explicit ``path`` the sidecar is written to ``$RADAR_DATA_DIR``
    when configured (so a read-only code layer is never touched), else next to
    the in-module config — matching where :func:`corner_ref_path` then reads it.
    """
    dest = path or _resolve_write(corner_ref_name_for(target))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.safe_dump(ref.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
    return dest


def save_config(cfg: RadarConfig, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # mode="json" turns tuples into lists, which is what yaml.safe_dump can emit.
    path.write_text(
        yaml.safe_dump(cfg.model_dump(mode="json"), sort_keys=False),
        encoding="utf-8",
    )
