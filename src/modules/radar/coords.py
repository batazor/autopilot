"""Expose the game↔canvas affine for the coordinate readout.

The stitcher writes the game→canvas affine into ``map_meta.json`` — derived from
the minimap, or, once the operator marks the kingdom corners, bundle-adjusted
onto the square game grid (``coord_source: "corners"``, see
:mod:`modules.radar.corners`). This thin reader surfaces it — with the inverse
precomputed so the browser only mat-vecs — for the ``/radar`` hover readout via
the tiles-meta endpoint.
"""

from __future__ import annotations

import json
from pathlib import Path

from modules.radar.georef import affine_from_meta, invert_affine
from modules.radar.stitch_georef import MAP_META_NAME


def coords_affine(run_dir: str | Path) -> dict | None:
    """The game↔canvas affine to expose to the viewer, or ``None`` when the run
    has no pinned origin (no absolute coordinates). Carries the forward affine,
    its precomputed inverse, and the source/accuracy tag from ``map_meta.json``
    (``corners`` once the grid is corner-locked, else ``derived``).
    """
    meta_path = Path(run_dir) / MAP_META_NAME
    if not meta_path.is_file():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    affine = affine_from_meta(meta)
    if affine is None:
        return None
    linear, offset = affine
    inv_linear, inv_offset = invert_affine(linear, offset)
    return {
        "game_to_canvas_linear": [[round(float(v), 6) for v in row] for row in linear],
        "game_to_canvas_offset": [round(float(offset[0]), 2), round(float(offset[1]), 2)],
        "canvas_to_game_linear": [[round(float(v), 8) for v in row] for row in inv_linear],
        "canvas_to_game_offset": [round(float(inv_offset[0]), 4), round(float(inv_offset[1]), 4)],
        "source": str(meta.get("coord_source") or "derived"),
        "residual_tiles_median": meta.get("coord_residual_tiles"),
    }
