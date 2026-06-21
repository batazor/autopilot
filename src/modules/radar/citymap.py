"""Assemble one persistent city map from several overlapping scans.

A base is bigger than a single scan, so the operator captures it in overlapping
chunks; this fuses them into ONE canvas + building registry the navigator can
localize against anywhere. Chunks are aligned by ORB between their stitched
canvases (robust to OCR noise in the names), composited into a single image,
and their building registries merged into the shared frame.

Output is written as a normal ``main_city`` run dir (``map_full.png`` +
``buildings.json`` + ``manifest.json``) so ``navigator.latest_city_run`` /
``Navigator.from_run`` load it with no special casing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import cv2
import numpy as np

from modules.radar.labels import (
    BUILDINGS_NAME,
    _canvas_offset,
    _dedup,
    _registry_dict,
)
from modules.radar.scanner import MANIFEST_NAME
from modules.radar.stitch import MAP_FULL_NAME

logger = logging.getLogger(__name__)


def assemble_city_map(runs_root: str | Path) -> dict:
    """Operator flow back-end: fuse every ``main_city`` scan chunk in
    ``runs_root`` into the stable ``citymap`` map the navigator loads."""
    from modules.radar.navigator import CITYMAP_DIRNAME, _is_city_run

    root = Path(runs_root)
    chunks = [
        d for d in root.iterdir()
        if d.is_dir() and d.name != CITYMAP_DIRNAME and _is_city_run(d)
    ]
    if not chunks:
        msg = "no main_city scans in runs_root to assemble — scan the base first"
        raise ValueError(msg)
    return build_city_map(sorted(chunks), root / CITYMAP_DIRNAME)


def _load_run(d: Path) -> tuple[np.ndarray, list[dict], dict] | None:
    img = cv2.imread(str(d / MAP_FULL_NAME))
    bj = d / BUILDINGS_NAME
    if img is None or not bj.is_file():
        return None
    builds = json.loads(bj.read_text(encoding="utf-8")).get("buildings") or []
    try:
        man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        man = {}
    return img, builds, man


def place_runs(
    runs: list[tuple[str, np.ndarray, list[dict]]],
) -> list[tuple[str, np.ndarray, tuple[float, float], list[dict]]]:
    """Align each scan canvas into one shared frame by ORB. Returns, per placed
    run, ``(name, canvas, registry_offset, buildings)`` where a canvas pixel maps
    to the shared frame by ``+ registry_offset``. The largest run anchors the
    frame; a run that overlaps no placed run (no shared imagery) is dropped."""
    ordered = sorted(runs, key=lambda r: -len(r[2]))
    placed: list[tuple[str, np.ndarray, tuple[float, float], list[dict]]] = []
    for name, canvas, builds in ordered:
        if not placed:
            placed.append((name, canvas, (0.0, 0.0), builds))
            continue
        off = None
        for _n, pimg, poff, _b in placed:
            sh = _canvas_offset(pimg, canvas)  # pimg px = canvas px + sh
            if sh is not None:
                off = (sh[0] + poff[0], sh[1] + poff[1])
                break
        if off is None:
            logger.warning("citymap: %s overlaps no placed chunk — dropped", name)
            continue
        placed.append((name, canvas, off, builds))
    return placed


def build_city_map(run_dirs: list[str | Path], out_dir: str | Path) -> dict:
    """Fuse ``run_dirs`` into one persistent city map under ``out_dir``.

    Writes ``map_full.png`` (composite), ``buildings.json`` (unified registry)
    and ``manifest.json`` (``config.target: main_city`` + crop + swipe scale from
    the base chunk) — i.e. a normal run the navigator loads directly.
    """
    out_dir = Path(out_dir)
    runs: list[tuple[str, np.ndarray, list[dict]]] = []
    base_man: dict = {}
    for d in run_dirs:
        d = Path(d)
        loaded = _load_run(d)
        if loaded is None:
            continue
        img, builds, man = loaded
        runs.append((d.name, img, builds))
        if not base_man:
            base_man = man
    if not runs:
        msg = "no usable scan runs (need map_full.png + buildings.json)"
        raise ValueError(msg)

    placed = place_runs(runs)
    # Composite bounds in the shared frame.
    min_x = min(off[0] for _n, _c, off, _b in placed)
    min_y = min(off[1] for _n, _c, off, _b in placed)
    max_x = max(off[0] + c.shape[1] for _n, c, off, _b in placed)
    max_y = max(off[1] + c.shape[0] for _n, c, off, _b in placed)
    width, height = int(round(max_x - min_x)), int(round(max_y - min_y))
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    for _n, chunk, (ox, oy), _b in placed:
        x, y = int(round(ox - min_x)), int(round(oy - min_y))
        h, w = chunk.shape[:2]
        roi = canvas[y : y + h, x : x + w]
        paint = chunk.any(axis=2)  # keep already-painted pixels, fill the rest
        roi[paint] = chunk[paint]

    dets = [
        {
            "name": b["name"],
            "confidence": b.get("confidence", 90.0),
            "canvas_px": [b["canvas_px"][0] + off[0] - min_x, b["canvas_px"][1] + off[1] - min_y],
        }
        for _n, _c, off, builds in placed
        for b in builds
    ]
    registry = _registry_dict(_dedup(dets))

    out_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_dir / MAP_FULL_NAME), canvas)
    (out_dir / BUILDINGS_NAME).write_text(json.dumps(registry, indent=2), encoding="utf-8")
    cfg = dict(base_man.get("config") or {})
    cfg["target"] = "main_city"
    manifest = {"config": cfg, "swipe_calibration": base_man.get("swipe_calibration")}
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info(
        "citymap: %d chunks → %dx%d canvas, %d buildings → %s",
        len(placed), width, height, registry["count"], out_dir,
    )
    return {
        "chunks": len(placed),
        "dropped": len(runs) - len(placed),
        "size": [width, height],
        "buildings": registry["count"],
        "out_dir": str(out_dir),
    }
