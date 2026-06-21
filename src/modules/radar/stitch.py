"""Assemble scanned frames into one canvas: ``map_full.png`` + a preview.

Registration is feature-based: the map has plenty of ORB keypoints (icons,
buildings, even snow has texture), so frame offsets are *measured* from
matched keypoints with a RANSAC translation fit instead of trusted from
navigation. Swipe drift and tap clamping therefore never reach the canvas —
they only change where the overlap happens to be.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path

import cv2
import numpy as np

from modules.radar.manifest import MANIFEST_NAME
from modules.radar.stitch_georef import MAP_META_NAME, _write_map_meta  # noqa: F401  (MAP_META_NAME re-exported)

# Registration math lives in stitch_matching; re-exported here so the public
# import surface (``from modules.radar.stitch import frames_consistent`` /
# ``move_prior`` / ``MatchEdge`` / the test helpers) stays stable after the
# split. ``frames_consistent`` and ``move_prior`` are used by the scanner too.
from modules.radar.stitch_matching import (  # noqa: F401  (re-exported)
    MatchEdge,
    _drop_outlier_edges,
    _feature_mask,
    _find_match_edges,
    _frame_mostly_outside,
    _orb_features,
    _orb_pair_offset,
    _refine_offset_phase,
    _seam_residuals,
    _solve_matched_positions,
    _useful_area_mask,
    _valid_content_mask,
    frames_consistent,
    move_prior,
)

logger = logging.getLogger(__name__)

# Serialize ``run_stitch`` per run directory: the live stitcher re-stitches
# mid-scan while the final pass runs after the scan ends, and a join timeout in
# the live loop can leave its pass still running when the final one starts.
# Without this they would write the same outputs at once. Combined with the
# unique temp names below, a run's map_full/preview/meta are never half-written.
_RUN_STITCH_GUARD = threading.Lock()
_RUN_STITCH_LOCKS: dict[str, threading.Lock] = {}


def _run_stitch_lock(run_dir: Path) -> threading.Lock:
    key = str(run_dir.resolve())
    with _RUN_STITCH_GUARD:
        lock = _RUN_STITCH_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _RUN_STITCH_LOCKS[key] = lock
        return lock


def _unique_tmp(run_dir: Path, name: str, suffix: str) -> Path:
    """A fresh, uniquely-named temp file in ``run_dir`` for an atomic replace.

    Per-call unique (mkstemp) so two stitches of the same run never write the
    same temp file, even if the per-run lock is bypassed (e.g. separate
    processes). The final ``Path.replace`` onto the real name stays atomic.
    """
    fd, path = tempfile.mkstemp(dir=run_dir, prefix=f".{name}.", suffix=suffix)
    os.close(fd)
    return Path(path)

PREVIEW_LONG_SIDE = 4096
MAP_FULL_NAME = "map_full.png"
MAP_PREVIEW_NAME = "map_preview.jpg"
DEFAULT_STITCH_VIEWPORT_W = 720
DEFAULT_STITCH_VIEWPORT_H = 1185


def _capture_size(manifest: dict, cfg: dict) -> tuple[int, int]:
    stitch_viewport = cfg.get("stitch_viewport")
    if isinstance(stitch_viewport, dict):
        w = int(stitch_viewport.get("w") or 0)
        h = int(stitch_viewport.get("h") or 0)
        if w > 0 and h > 0:
            return w, h
    frame_size = manifest.get("frame_size")
    if isinstance(frame_size, dict):
        w = int(frame_size.get("w") or 0)
        h = int(frame_size.get("h") or 0)
        if w > 0 and h > 0:
            return w, h
    screen = cfg.get("screen")
    if isinstance(screen, dict):
        w = int(screen.get("w") or 0)
        h = int(screen.get("h") or 0)
        if w > 0 and h > 0:
            return w, h
    return DEFAULT_STITCH_VIEWPORT_W, DEFAULT_STITCH_VIEWPORT_H


def _integrated_nominal(
    entries: list[dict],
    right: tuple[float, float],
    down: tuple[float, float],
) -> list[tuple[float, float]]:
    """Nominal layout from the swipes navigation actually performed.

    The theoretical ``ix*right + iy*down`` grid lies whenever a move was
    shortened or zeroed (border guard, pan clamp): it pulls frames captured
    from the same spot a full step apart and warps the solve. Chaining each
    entry's swipe prior instead keeps the nominal consistent with what the
    camera really did; the grid step is only the fallback for moves with no
    usable prior (teleports, pre-prior manifests). The first entry stays at
    its grid position so the canvas keeps the same global frame.
    """
    positions: list[tuple[float, float]] = []
    for k, entry in enumerate(entries):
        if k == 0:
            positions.append((
                entry["ix"] * right[0] + entry["iy"] * down[0],
                entry["ix"] * right[1] + entry["iy"] * down[1],
            ))
            continue
        prior = move_prior(entry)
        if prior is None:
            prev = entries[k - 1]
            dix = entry["ix"] - prev["ix"]
            diy = entry["iy"] - prev["iy"]
            prior = (dix * right[0] + diy * down[0], dix * right[1] + diy * down[1])
        positions.append((positions[-1][0] + prior[0], positions[-1][1] + prior[1]))
    return positions


def _corner_residual_tiles(
    positions: list[tuple[float, float]],
    affine: tuple[tuple[tuple[float, float], tuple[float, float]], tuple[float, float]] | None,
    constraints: list[tuple[int, tuple[float, float], tuple[float, float]]],
) -> float | None:
    """Median corner-fit error in game tiles: how far each marked corner's solved
    canvas position lands from where the affine maps its game vertex. ``None``
    without corners — this is the readout's honest accuracy after pinning."""
    if not affine or not constraints:
        return None
    (a, b), (d, e) = affine[0]
    ox, oy = affine[1]
    px_per_tile = (float(np.hypot(a, d)) + float(np.hypot(b, e))) / 2.0 or 1.0
    res: list[float] = []
    for idx, (fpx, fpy), (gx, gy) in constraints:
        if idx >= len(positions):
            continue
        cx, cy = positions[idx][0] + fpx, positions[idx][1] + fpy
        ax, ay = a * gx + b * gy + ox, d * gx + e * gy + oy
        res.append(float(np.hypot(cx - ax, cy - ay)) / px_per_tile)
    return float(np.median(res)) if res else None


def run_stitch(run_dir: Path, *, preview_long_side: int = PREVIEW_LONG_SIDE) -> Path:
    """Stitch a run, serialized against any concurrent stitch of the same run.

    ``preview_long_side`` caps the JPEG preview's long edge — the live stitcher
    passes a smaller value so mid-scan re-stitches stay cheap, while the final
    pass keeps the full-resolution default.
    """
    with _run_stitch_lock(run_dir):
        return _run_stitch_locked(run_dir, preview_long_side=preview_long_side)


def _run_stitch_locked(run_dir: Path, *, preview_long_side: int = PREVIEW_LONG_SIDE) -> Path:
    manifest_path = run_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        msg = f"{manifest_path} not found — run `radar scan` first"
        raise FileNotFoundError(msg)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cfg = manifest["config"]
    overlap = float(cfg["overlap"])
    capture_w, capture_h = _capture_size(manifest, cfg)
    step_x = capture_w * (1.0 - overlap)
    step_y = capture_h * (1.0 - overlap)

    # Order by the persisted capture index, not dict iteration order: the
    # consecutive-pair matching (and each frame's swipe prior) is keyed to
    # capture sequence, and JSON object key order is not a guaranteed contract.
    # Manifests written before ``order`` existed fall back to insertion order.
    raw_frames = list(manifest["frames"].values())
    entries = sorted(
        enumerate(raw_frames),
        key=lambda iv: (iv[1].get("order", iv[0]), iv[0]),
    )
    entries = [e for _, e in entries]
    if not entries:
        msg = f"{manifest_path} contains no frames"
        raise ValueError(msg)

    # The kingdom-edge masking blacks out dark terrain it judges to be
    # "outside the world". On this game's map legitimate terrain is dark too,
    # so it over-cuts; keep it off unless a scan explicitly opts in. The crop
    # already excludes the HUD, and featureless out-of-world black yields no
    # ORB keypoints anyway, so leaving it in costs only a thin true-edge band.
    mask_outside = bool(cfg.get("mask_outside_border"))
    images: list[np.ndarray | None] = []
    masks: list[np.ndarray | None] = []
    missing = 0
    for entry in entries:
        img = cv2.imread(str(run_dir / entry["file"]))
        if img is None:
            missing += 1
            images.append(None)
            masks.append(None)
            continue
        images.append(img)
        masks.append(_valid_content_mask(img) if mask_outside else None)
    if missing:
        logger.warning("%d frame file(s) listed in the manifest are missing on disk", missing)
    if mask_outside:
        dropped = []
        for k, (img, mask) in enumerate(zip(images, masks, strict=True)):
            if img is None or mask is None:
                continue
            if _frame_mostly_outside(img, mask, cfg.get("crop")):
                images[k] = None
                masks[k] = None
                dropped.append(f"{entries[k]['ix']:02d}_{entries[k]['iy']:02d}")
        if dropped:
            logger.warning(
                "%d frame(s) are mostly outside the kingdom and were dropped "
                "from registration and paste: %s",
                len(dropped), ", ".join(dropped),
            )
    if all(img is None for img in images):
        msg = f"none of the {len(entries)} manifest frames could be read from {run_dir}"
        raise ValueError(msg)

    # Feature-based registration: ORB keypoints (icons, buildings, snow
    # texture) matched per pair give the real frame offsets — no trust in
    # navigation. The world view is isometric (a minimap grid step shifts the
    # screen diagonally), so even the nominal layout uses the *measured*
    # right/down vectors; axis-aligned geometry is only the no-match fallback.
    features = [
        _orb_features(img, _feature_mask(img, mask, cfg.get("crop")))
        if img is not None
        else None
        for img, mask in zip(images, masks, strict=True)
    ]
    edges, right, down = _find_match_edges(
        entries,
        features,
        images,
        cfg.get("crop"),
        fallback_right=(step_x, 0.0),
        fallback_down=(0.0, step_y),
    )
    # Operator-marked corners (if any) pin the grid to the square game-coordinate
    # lattice: the solve places their frames so each corner lands on its known
    # game vertex, spreading the accumulated drift out over the edge graph.
    from modules.radar.corners import load_corner_constraints

    corner_constraints = load_corner_constraints(run_dir, entries)
    nominal_positions = _integrated_nominal(entries, right, down)
    positions, corner_affine = _solve_matched_positions(
        nominal_positions, images, edges, corner_constraints,
    )
    # Robust passes: a low-score edge hundreds of px off the consensus is an
    # aliased match, not a seam — drop it and re-solve. Iterated because
    # removing one wrong edge can unmask the next (the first solve splits the
    # contradiction between them); strong edges are never dropped, so this
    # converges in a couple of rounds.
    for _ in range(3):
        kept_edges = _drop_outlier_edges(entries, positions, edges)
        if len(kept_edges) == len(edges):
            break
        edges = kept_edges
        positions, corner_affine = _solve_matched_positions(
            nominal_positions, images, edges, corner_constraints,
        )
    seam_report = _seam_residuals(entries, positions, edges)
    corner_residual_tiles = _corner_residual_tiles(positions, corner_affine, corner_constraints)
    if corner_constraints:
        logger.info(
            "radar: stitch pinned to %d operator corner(s), residual %.1f tiles",
            len(corner_constraints),
            corner_residual_tiles if corner_residual_tiles is not None else float("nan"),
        )

    # Frames are saved as-is (full screenshots) so coordinates stay in one
    # system, but only the crop region — game world without the HUD (top bar,
    # bottom chat/nav, right-side buttons) — is pasted onto the canvas. The
    # cut-off margins are always covered by a neighbouring frame's crop
    # region; only the outer border of the whole map loses them, and the
    # canvas is trimmed to painted content at the end.
    placed = [
        (img, _useful_area_mask(img, mask, cfg.get("crop")), pos)
        for img, mask, pos in zip(images, masks, positions, strict=True)
        if img is not None
    ]
    off_x = min(px for _, _, (px, _) in placed)
    off_y = min(py for _, _, (_, py) in placed)
    canvas_w = max(int(round(px - off_x)) + img.shape[1] for img, _, (px, _) in placed)
    canvas_h = max(int(round(py - off_y)) + img.shape[0] for img, _, (_, py) in placed)
    logger.info("canvas %d×%d from %d frames", canvas_w, canvas_h, len(placed))
    # Max-weight mosaic: per pixel keep the single frame whose centre is nearest
    # (largest distance-to-mask-edge), so the image stays SHARP — no averaging,
    # which on a high-overlap animated scene (fire, snow, slight misalignment)
    # would blur everything. The seam between two frames falls on the line where
    # their edge-distances are equal (midway through the overlap), which reads
    # far softer than the rectangular step of a hard last-writer overwrite.
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    best = np.full((canvas_h, canvas_w), -1.0, dtype=np.float32)
    painted = np.zeros((canvas_h, canvas_w), dtype=bool)
    for img, paste_mask, (px, py) in placed:
        x = int(round(px - off_x))
        y = int(round(py - off_y))
        h, w = img.shape[:2]
        m = paste_mask > 0
        dist = cv2.distanceTransform(m.astype(np.uint8), cv2.DIST_L2, 3)
        sub_best = best[y : y + h, x : x + w]
        win = m & (dist > sub_best)
        canvas[y : y + h, x : x + w][win] = img[win]
        sub_best[win] = dist[win]
        painted[y : y + h, x : x + w] |= m

    ys, xs = np.where(painted)
    trim_x, trim_y = (int(xs.min()), int(ys.min())) if len(xs) else (0, 0)
    if len(xs):
        canvas = canvas[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    canvas_h, canvas_w = canvas.shape[:2]

    try:
        _write_map_meta(
            run_dir, cfg, entries, positions,
            origin=(off_x + trim_x, off_y + trim_y), right=right, down=down,
            seam=seam_report, corner_affine=corner_affine,
            corner_residual_tiles=corner_residual_tiles,
        )
    except Exception:
        logger.exception("radar: map_meta.json not written (georeference failed)")

    # Atomic writes: during a scan the live stitcher rewrites these every few
    # seconds while the API serves the preview — readers must never see a
    # half-written file.
    full_path = run_dir / MAP_FULL_NAME
    full_tmp = _unique_tmp(run_dir, MAP_FULL_NAME, ".png")
    if not cv2.imwrite(str(full_tmp), canvas):
        full_tmp.unlink(missing_ok=True)
        msg = f"failed to write {full_path}"
        raise RuntimeError(msg)
    full_tmp.replace(full_path)

    scale = preview_long_side / max(canvas_w, canvas_h)
    preview = canvas
    if scale < 1.0:
        preview = cv2.resize(
            canvas,
            (int(canvas_w * scale), int(canvas_h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    preview_path = run_dir / MAP_PREVIEW_NAME
    preview_tmp = _unique_tmp(run_dir, MAP_PREVIEW_NAME, ".jpg")
    cv2.imwrite(str(preview_tmp), preview)
    preview_tmp.replace(preview_path)
    logger.info("stitched map saved: %s (+ %s)", full_path, preview_path)
    return full_path
