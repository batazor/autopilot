"""Run manifest: the per-run ``manifest.json`` plus frame naming + the shared
scan exception.

A leaf module (no other radar-internal imports) so both the scanner and the
stitcher can depend on it without an import cycle. ``MANIFEST_NAME`` and
``ScanAborted`` are re-exported from :mod:`modules.radar.scanner` for backward
compatibility with existing call sites and tests.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from modules.radar.config import RadarConfig
    from modules.radar.geometry import GridPoint

MANIFEST_NAME = "manifest.json"


class ScanAborted(RuntimeError):
    """Unrecoverable scan failure (stale calibration, lost minimap, …)."""


def frame_key(ix: int, iy: int) -> str:
    return f"{ix:02d}_{iy:02d}"


def frame_filename(ix: int, iy: int) -> str:
    return f"frame_{ix:02d}_{iy:02d}.png"


def load_manifest(out_dir: Path, cfg: RadarConfig, grid: list[GridPoint]) -> dict:
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


def save_manifest(out_dir: Path, manifest: dict) -> None:
    path = out_dir / MANIFEST_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    tmp.replace(path)
