"""Main scan loop: tap the minimap grid → frames + ``manifest.json``.

Camera movement is minimap taps only — the view jump-cuts to each grid cell.
No swipes: drag inertia accumulates drift frame-to-frame and twitches the
screen mid-capture, while a tap's placement error is bounded by minimap
resolution and does not accumulate.
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

from modules.radar.config import load_config
from modules.radar.device import RadarDevice, pick_serial
from modules.radar.geometry import Affine, generate_grid, limit_grid_centered

if TYPE_CHECKING:
    from pathlib import Path

    from modules.radar.config import RadarConfig
    from modules.radar.events import RadarEventPublisher
    from modules.radar.geometry import GridPoint

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


class ScanAborted(RuntimeError):
    """Unrecoverable scan failure (stale calibration, lost minimap, …)."""


def build_scan_grid(cfg: RadarConfig) -> list[GridPoint]:
    """Tap-point route for a scan — single source of truth for scanner + API.

    Serpentine raster order: taps are absolute jumps, so route length does not
    matter for accuracy — the raster just keeps the progress display readable.
    """
    corners = cfg.minimap.corners.as_geometry()
    grid = generate_grid(
        corners,
        cfg.viewport.rect_w,
        cfg.viewport.rect_h,
        overlap=cfg.overlap,
        edge_margin_px=cfg.edge_margin_px,
    )
    if cfg.grid_limit is not None:
        grid = limit_grid_centered(grid, corners, cfg.grid_limit.cols, cfg.grid_limit.rows)
    return grid


def frame_key(ix: int, iy: int) -> str:
    return f"{ix:02d}_{iy:02d}"


def frame_filename(ix: int, iy: int) -> str:
    return f"frame_{ix:02d}_{iy:02d}.png"


# ---------------------------------------------------------------------------
# Stabilization
# ---------------------------------------------------------------------------


def _central_region(frame: np.ndarray, cfg: RadarConfig) -> np.ndarray:
    """Middle third of the configured game-area crop — where map content moves."""
    c = cfg.crop
    x0 = c.x + c.w // 3
    y0 = c.y + c.h // 3
    return frame[y0 : y0 + c.h // 3, x0 : x0 + c.w // 3]


def wait_stable(device: RadarDevice, cfg: RadarConfig) -> tuple[np.ndarray, bool]:
    """Capture until two consecutive frames stop differing, or time out.

    Returns ``(last_frame, stable)``; on timeout the frame is still returned
    so the caller can save it with an ``unstable`` flag.
    """
    t = cfg.timings
    deadline = time.monotonic() + t.stabilize_timeout_ms / 1000.0
    prev = device.capture()
    hits = 0
    while time.monotonic() < deadline:
        time.sleep(t.stabilize_interval_ms / 1000.0)
        cur = device.capture()
        diff = float(np.mean(cv2.absdiff(_central_region(prev, cfg), _central_region(cur, cfg))))
        prev = cur
        if diff <= t.stabilize_diff_threshold:
            hits += 1
            if hits >= t.stabilize_consecutive:
                return cur, True
        else:
            hits = 0
    logger.warning("frame did not stabilize within %d ms", t.stabilize_timeout_ms)
    return prev, False


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------


def _load_manifest(out_dir: Path, cfg: RadarConfig, grid: list[GridPoint]) -> dict:
    path = out_dir / MANIFEST_NAME
    cfg_dump = cfg.model_dump(mode="json")
    if path.is_file():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        if manifest.get("config") != cfg_dump:
            msg = (
                f"{path} was produced with a different radar config — "
                "frame indices would not be comparable; use a fresh --out directory"
            )
            raise ScanAborted(msg)
        return manifest
    return {
        "config": cfg_dump,
        # Cell list is stored (not just the count) so the UI can draw the
        # diamond layout for a finished or in-progress run without recomputing
        # the grid geometry client-side.
        "grid": {
            "count": len(grid),
            "points": [{"ix": p.ix, "iy": p.iy} for p in grid],
        },
        "frames": {},
    }


def _save_manifest(out_dir: Path, manifest: dict) -> None:
    path = out_dir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Scan loop
# ---------------------------------------------------------------------------


def run_scan(
    config_path: Path,
    out_dir: Path,
    *,
    serial: str | None = None,
    adb_bin: str | None = None,
    events: RadarEventPublisher | None = None,
) -> None:
    cfg = load_config(config_path)
    bin_pref = adb_bin or cfg.adb_bin
    device_serial = serial or cfg.device_serial or pick_serial(bin_pref)
    device = RadarDevice(device_serial, bin_pref)

    corners = cfg.minimap.corners.as_geometry()
    grid = build_scan_grid(cfg)
    affine = Affine.from_corners(corners, cfg.game_size)

    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(out_dir, cfg, grid)
    started = time.monotonic()
    if events is not None:
        events.scan_started(len(grid), [(p.ix, p.iy) for p in grid])
    try:
        _scan_grid(device, cfg, grid, affine, manifest, out_dir, events)
    except Exception as exc:
        if events is not None:
            events.scan_failed(str(exc))
        raise
    if events is not None:
        events.scan_finished(time.monotonic() - started)


def _scan_grid(
    device: RadarDevice,
    cfg: RadarConfig,
    grid: list[GridPoint],
    affine: Affine,
    manifest: dict,
    out_dir: Path,
    events: RadarEventPublisher | None,
) -> None:
    frames: dict = manifest["frames"]

    done = 0
    skipped = 0
    unstable = 0
    total = len(grid)
    for point in grid:
        key = frame_key(point.ix, point.iy)
        filename = frame_filename(point.ix, point.iy)
        if key in frames and (out_dir / filename).is_file():
            skipped += 1
            if events is not None:
                # Resumed run: replay already-present frames so the UI's
                # progress diamond prefills instead of starting empty.
                events.frame_done(
                    point.ix,
                    point.iy,
                    unstable=bool(frames[key].get("unstable")),
                    done=done + skipped,
                    total=total,
            )
            continue

        device.tap(point.x, point.y)
        time.sleep(cfg.timings.post_tap_delay_ms / 1000.0)
        frame, stable = wait_stable(device, cfg)
        if not stable:
            unstable += 1
        manifest.setdefault("frame_size", {"w": int(frame.shape[1]), "h": int(frame.shape[0])})

        # Save the frame as-is (no UI crop): one coordinate system for capture
        # and stitch. The HUD bakes into tiles for now — accepted trade-off
        # while the placement geometry is being tuned.
        if not cv2.imwrite(str(out_dir / filename), frame):
            msg = f"failed to write {out_dir / filename}"
            raise ScanAborted(msg)
        frames[key] = {
            "ix": point.ix,
            "iy": point.iy,
            "tap_px": [point.x, point.y],
            "planned_game_xy": [round(v, 2) for v in affine.to_game((point.x, point.y))],
            "file": filename,
            "unstable": not stable,
            "ts": time.time(),
        }
        _save_manifest(out_dir, manifest)
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
        "scan complete: %d captured, %d already present, %d unstable → %s",
        done,
        skipped,
        unstable,
        out_dir,
    )


