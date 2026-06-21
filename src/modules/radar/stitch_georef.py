"""Georeference the stitched map: canvas pixels ↔ absolute game coordinates.

Combines the measured right/down basis (canvas px per grid step), the minimap
affine (grid step → game tiles) and the border crossings the origin servo
measured (bottom corner = game ``(G-1, G-1)``, optional top corner = ``(0, 0)``)
into the linear map written to ``map_meta.json``. Best-effort: without minimap
calibration or an anchored origin the file simply carries less information.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
from pathlib import Path

import numpy as np

from modules.radar.geometry import Affine, Corners

logger = logging.getLogger(__name__)

MAP_META_NAME = "map_meta.json"


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via a uniquely-named temp file + atomic replace.

    Unique temp name so a live mid-scan stitch and the final stitch never write
    the same temp file; the replace onto the real name stays atomic for readers.
    """
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp)
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


def _write_map_meta(
    run_dir: Path,
    cfg: dict,
    entries: list[dict],
    positions: list[tuple[float, float]],
    origin: tuple[float, float],
    right: tuple[float, float],
    down: tuple[float, float],
    seam: dict | None = None,
    corner_affine: tuple[tuple[tuple[float, float], tuple[float, float]], tuple[float, float]] | None = None,
    corner_residual_tiles: float | None = None,
) -> None:
    """Georeference the stitched map: canvas px ↔ absolute game coordinates.

    Ingredients pinning the full linear map:
    - the measured right/down basis (canvas px per grid step) combined with the
      minimap affine (grid step → game tiles) gives the 2×2 linear part;
    - the border crossing the origin servo measured in the first frame is the
      map's bottom corner — game ``(G-1, G-1)`` — and fixes the translation;
    - when the scan also recorded the TOP corner crossing (game ``(0, 0)``),
      the 2×2 part is corrected by the similarity that takes the predicted
      diagonal onto the measured one — accumulated row-by-row drift in the
      solved positions then cancels instead of scaling the whole map.
    Written best-effort: without minimap calibration or an anchored origin the
    file simply carries less (or no) information.
    """
    mm = cfg.get("minimap") or {}
    corners_raw = mm.get("corners") or {}
    viewport = cfg.get("viewport") or {}
    if not corners_raw or not viewport:
        return
    corners = Corners(
        top=tuple(corners_raw["top"]),
        right=tuple(corners_raw["right"]),
        bottom=tuple(corners_raw["bottom"]),
        left=tuple(corners_raw["left"]),
    )
    game_size = int(cfg.get("game_size") or 1200)
    affine = Affine.from_corners(corners, game_size)
    overlap = float(cfg.get("overlap") or 0.5)
    step_x = float(viewport["rect_w"]) * (1.0 - overlap)
    step_y = float(viewport["rect_h"]) * (1.0 - overlap)
    # Game-tile delta of one grid step: the linear part of the minimap→game
    # affine applied to the step vectors (any base point cancels out).
    base = affine.to_game(corners.top)
    g_right = np.array(affine.to_game((corners.top[0] + step_x, corners.top[1]))) - base
    g_down = np.array(affine.to_game((corners.top[0], corners.top[1] + step_y))) - base
    game_basis = np.column_stack([g_right, g_down])
    if abs(float(np.linalg.det(game_basis))) < 1e-9:
        return
    screen_basis = np.column_stack([np.array(right), np.array(down)])
    linear = screen_basis @ np.linalg.inv(game_basis)  # game Δ → canvas Δ

    meta: dict = {"game_size": game_size}
    if seam is not None:
        meta["seam_check"] = seam
    # Per-frame canvas placement (top-left of the full frame on the trimmed
    # canvas). A feature at frame-pixel (fx, fy) — e.g. a building name label —
    # sits at canvas (canvas_px[0] + fx, canvas_px[1] + fy); this is what the
    # building registry uses to turn per-frame detections into map coordinates.
    meta["frames"] = {
        f"{int(e['ix']):02d}_{int(e['iy']):02d}": {
            "canvas_px": [round(p[0] - origin[0], 1), round(p[1] - origin[1], 1)],
        }
        for e, p in zip(entries, positions, strict=True)
    }
    bottom_canvas: np.ndarray | None = None
    bottom_entry: dict | None = None
    top_canvas: np.ndarray | None = None
    top_entry: dict | None = None
    for entry, pos in zip(entries, positions, strict=True):
        move = entry.get("move") or {}
        apex = move.get("border_apex_px")
        if bottom_canvas is None and move.get("origin") and apex:
            bottom_canvas = np.array([pos[0] - origin[0] + apex[0], pos[1] - origin[1] + apex[1]])
            bottom_entry = entry
        cross = entry.get("top_cross_px")
        if top_canvas is None and cross:
            top_canvas = np.array([pos[0] - origin[0] + cross[0], pos[1] - origin[1] + cross[1]])
            top_entry = entry

    if bottom_canvas is not None:
        corner_game = np.array([game_size - 1.0, game_size - 1.0])
        if top_canvas is not None:
            # Both kingdom corners measured: the game diagonal (0,0)→(G-1,G-1)
            # must land exactly on the canvas segment top→bottom crossing.
            # Fit the similarity (a, b) taking the predicted diagonal onto the
            # measured one and fold it into the linear part — sanity-gated:
            # a wildly off correction means a mis-detected corner, not drift.
            pred = linear @ corner_game
            meas = bottom_canvas - top_canvas
            denom = float(pred @ pred)
            if denom > 1e-9:
                a = float(pred @ meas) / denom
                b = float(pred[0] * meas[1] - pred[1] * meas[0]) / denom
                scale = math.hypot(a, b)
                rotation_deg = math.degrees(math.atan2(b, a))
                if abs(scale - 1.0) <= 0.15 and abs(rotation_deg) <= 5.0:
                    linear = np.array([[a, -b], [b, a]]) @ linear
                    meta["anchor_correction"] = {
                        "scale": round(scale, 5),
                        "rotation_deg": round(rotation_deg, 3),
                    }
                    logger.info(
                        "map meta: two-corner correction applied (scale %.4f, rot %.2f°)",
                        scale, rotation_deg,
                    )
                else:
                    logger.warning(
                        "map meta: two-corner correction rejected (scale %.3f, rot %.1f°) "
                        "— one of the corners is likely mis-detected",
                        scale, rotation_deg,
                    )
            meta["top_anchor"] = {
                "frame": top_entry.get("file"),
                "cross_frame_px": top_entry["top_cross_px"],
                "canvas_px": [round(float(v), 1) for v in top_canvas],
                "game_xy": [0, 0],
            }
        apex = (bottom_entry.get("move") or {}).get("border_apex_px")
        offset = bottom_canvas - linear @ corner_game
        meta["anchor"] = {
            "frame": bottom_entry.get("file"),
            "apex_frame_px": [round(float(apex[0]), 1), round(float(apex[1]), 1)],
            "canvas_px": [round(float(v), 1) for v in bottom_canvas],
            "game_xy": [game_size - 1, game_size - 1],
        }
        meta["game_to_canvas_offset"] = [round(float(v), 2) for v in offset]
    meta["game_to_canvas_linear"] = [[round(float(v), 6) for v in row] for row in linear]

    # Operator-marked corners pin the grid: the joint solve returned a
    # game→canvas affine in solve-coords; shift its offset by the canvas origin
    # (the derived map above is overridden — corners are ground truth, not a
    # minimap guess) and tag the source so the readout reports it as corner-locked.
    if corner_affine is not None:
        clin, coff = corner_affine
        meta["game_to_canvas_linear"] = [[round(float(v), 6) for v in row] for row in clin]
        meta["game_to_canvas_offset"] = [
            round(float(coff[0] - origin[0]), 2),
            round(float(coff[1] - origin[1]), 2),
        ]
        meta["coord_source"] = "corners"
        if corner_residual_tiles is not None:
            meta["coord_residual_tiles"] = round(float(corner_residual_tiles), 3)

    path = run_dir / MAP_META_NAME
    _atomic_write_text(path, json.dumps(meta, indent=2))
    logger.info("map meta saved: %s (anchor: %s)", path, "yes" if "anchor" in meta else "no")

