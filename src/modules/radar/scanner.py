"""Main scan loop: swipe/tap minimap grid → frames + ``manifest.json``."""

from __future__ import annotations

import json
import logging
import math
import time
from typing import TYPE_CHECKING

import cv2
import numpy as np

from modules.radar.config import load_config
from modules.radar.device import RadarDevice, pick_serial
from modules.radar.geometry import Affine, generate_grid, order_grid_center_first

if TYPE_CHECKING:
    from pathlib import Path

    from modules.radar.config import RadarConfig
    from modules.radar.events import RadarEventPublisher
    from modules.radar.geometry import GridPoint

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


class ScanAborted(RuntimeError):
    """Unrecoverable scan failure (stale calibration, lost minimap, …)."""


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
    grid = generate_grid(
        corners,
        cfg.viewport.rect_w,
        cfg.viewport.rect_h,
        overlap=cfg.overlap,
        edge_margin_px=cfg.edge_margin_px,
    )
    if cfg.navigation.mode == "swipe":
        grid = order_grid_center_first(grid, corners)
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

    c = cfg.crop
    done = 0
    skipped = 0
    unstable = 0
    total = len(grid)
    previous: GridPoint | None = None
    for point in grid:
        key = frame_key(point.ix, point.iy)
        filename = frame_filename(point.ix, point.iy)
        if key in frames and (out_dir / filename).is_file():
            skipped += 1
            previous = point
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

        move_meta = _move_to_point(device, cfg, previous, point)
        previous = point
        time.sleep(cfg.timings.post_tap_delay_ms / 1000.0)
        frame, stable = wait_stable(device, cfg)
        if not stable:
            unstable += 1
        manifest.setdefault("frame_size", {"w": int(frame.shape[1]), "h": int(frame.shape[0])})

        crop = frame[c.y : c.y + c.h, c.x : c.x + c.w]
        if not cv2.imwrite(str(out_dir / filename), crop):
            msg = f"failed to write {out_dir / filename}"
            raise ScanAborted(msg)
        frames[key] = {
            "ix": point.ix,
            "iy": point.iy,
            "tap_px": [point.x, point.y],
            "move": move_meta,
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


def _move_to_point(
    device: RadarDevice,
    cfg: RadarConfig,
    previous: GridPoint | None,
    point: GridPoint,
) -> dict:
    if cfg.navigation.mode == "tap":
        device.tap(point.x, point.y)
        return {"mode": "tap", "target_px": [round(point.x, 2), round(point.y, 2)]}
    if previous is None:
        return {"mode": "swipe", "origin": True}
    dx = point.x - previous.x
    dy = point.y - previous.y
    swipes = _swipe_relative(device, cfg, dx, dy)
    return {
        "mode": "swipe",
        "from": [previous.ix, previous.iy],
        "delta_minimap_px": [round(dx, 2), round(dy, 2)],
        "swipes": swipes,
    }


def _swipe_relative(
    device: RadarDevice,
    cfg: RadarConfig,
    minimap_dx: float,
    minimap_dy: float,
) -> list[dict[str, int]]:
    if abs(minimap_dx) < 1e-6 and abs(minimap_dy) < 1e-6:
        return []

    c = cfg.crop
    nav = cfg.navigation
    viewport_w = cfg.stitch_viewport.w if cfg.stitch_viewport is not None else c.w
    viewport_h = cfg.stitch_viewport.h if cfg.stitch_viewport is not None else c.h
    # To move the camera right/down on the map, drag the map left/up.
    finger_dx = -(minimap_dx / cfg.viewport.rect_w) * viewport_w * nav.swipe_scale
    finger_dy = -(minimap_dy / cfg.viewport.rect_h) * viewport_h * nav.swipe_scale

    margin_x = min(nav.swipe_margin_px, max(0, c.w // 2 - 1))
    margin_y = min(nav.swipe_margin_px, max(0, c.h // 2 - 1))
    max_dx = max(1, c.w - margin_x * 2)
    max_dy = max(1, c.h - margin_y * 2)
    chunks = max(1, math.ceil(max(abs(finger_dx) / max_dx, abs(finger_dy) / max_dy)))
    step_x = finger_dx / chunks
    step_y = finger_dy / chunks

    emitted: list[dict[str, int]] = []
    for _ in range(chunks):
        x1, y1, x2, y2 = _swipe_points_for_delta(
            c.x,
            c.y,
            c.w,
            c.h,
            margin_x,
            margin_y,
            step_x,
            step_y,
        )
        device.swipe(x1, y1, x2, y2, nav.swipe_duration_ms)
        emitted.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "ms": nav.swipe_duration_ms})
    return emitted


def _swipe_points_for_delta(
    crop_x: int,
    crop_y: int,
    crop_w: int,
    crop_h: int,
    margin_x: int,
    margin_y: int,
    dx: float,
    dy: float,
) -> tuple[int, int, int, int]:
    x1 = crop_x + margin_x if dx >= 0 else crop_x + crop_w - margin_x
    y1 = crop_y + margin_y if dy >= 0 else crop_y + crop_h - margin_y
    x2 = x1 + dx
    y2 = y1 + dy
    min_x, max_x = crop_x + margin_x, crop_x + crop_w - margin_x
    min_y, max_y = crop_y + margin_y, crop_y + crop_h - margin_y
    return (
        int(round(min(max(x1, min_x), max_x))),
        int(round(min(max(y1, min_y), max_y))),
        int(round(min(max(x2, min_x), max_x))),
        int(round(min(max(y2, min_y), max_y))),
    )
