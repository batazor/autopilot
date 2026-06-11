"""Slippy-map tile pyramid for a stitched run (Pillow only, no GDAL).

``tiles/{z}/{x}/{y}.png`` inside the run directory plus ``tiles.json`` with the
pyramid metadata. ``z = max_zoom`` is the native ``map_full.png`` resolution;
every level below halves it down to ``z = 0`` where the whole map fits one
tile. Idempotent: existing tiles are skipped on re-run.
"""

from __future__ import annotations

import json
import logging
import math
from typing import TYPE_CHECKING

from PIL import Image

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

TILE_SIZE = 256
TILES_DIR_NAME = "tiles"
TILES_META_NAME = "tiles.json"
MAP_FULL_NAME = "map_full.png"

# Stitched kingdom canvases legitimately exceed Pillow's default
# decompression-bomb threshold (~89 MP); raise the cap for our own files.
_MAX_PIXELS = 512_000_000


def max_zoom_for(width: int, height: int, tile_size: int = TILE_SIZE) -> int:
    """Smallest z where the full image fits its native resolution."""
    if width <= 0 or height <= 0:
        msg = f"image dimensions must be positive, got {width}×{height}"
        raise ValueError(msg)
    return max(0, math.ceil(math.log2(max(width, height) / tile_size)))


def generate_tiles(run_dir: Path, *, tile_size: int = TILE_SIZE) -> dict:
    """Build (or complete) the tile pyramid for ``run_dir``; returns the metadata."""
    src = run_dir / MAP_FULL_NAME
    if not src.is_file():
        msg = f"{src} not found — the run must be stitched before tiling"
        raise FileNotFoundError(msg)
    Image.MAX_IMAGE_PIXELS = max(Image.MAX_IMAGE_PIXELS or 0, _MAX_PIXELS)
    img = Image.open(src).convert("RGB")
    src_mtime_ns = src.stat().st_mtime_ns
    width, height = img.size
    mz = max_zoom_for(width, height, tile_size)
    tiles_root = run_dir / TILES_DIR_NAME
    written = 0
    skipped = 0
    for z in range(mz, -1, -1):
        scale = 2 ** (mz - z)
        lw = math.ceil(width / scale)
        lh = math.ceil(height / scale)
        level = img if z == mz else img.resize((lw, lh), Image.Resampling.LANCZOS)
        for ty in range(math.ceil(lh / tile_size)):
            for tx in range(math.ceil(lw / tile_size)):
                out = tiles_root / str(z) / str(tx) / f"{ty}.png"
                if out.is_file() and out.stat().st_mtime_ns >= src_mtime_ns:
                    skipped += 1
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                box = (
                    tx * tile_size,
                    ty * tile_size,
                    min((tx + 1) * tile_size, lw),
                    min((ty + 1) * tile_size, lh),
                )
                tile = level.crop(box)
                if tile.size != (tile_size, tile_size):
                    # Edge tile: pad to the full size with transparency so the
                    # viewer never stretches a partial tile.
                    padded = Image.new("RGBA", (tile_size, tile_size), (0, 0, 0, 0))
                    padded.paste(tile, (0, 0))
                    tile = padded
                tile.save(out)
                written += 1
    meta = {
        "width": width,
        "height": height,
        "min_zoom": 0,
        "max_zoom": mz,
        "tile_size": tile_size,
    }
    (run_dir / TILES_META_NAME).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    logger.info(
        "tile pyramid for %s: %d written, %d already present (z 0..%d)",
        run_dir.name,
        written,
        skipped,
        mz,
    )
    return meta
