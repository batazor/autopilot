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
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from modules.radar.scanner import MANIFEST_NAME

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

PREVIEW_LONG_SIDE = 4096
MAP_FULL_NAME = "map_full.png"
MAP_PREVIEW_NAME = "map_preview.jpg"
DEFAULT_STITCH_VIEWPORT_W = 720
DEFAULT_STITCH_VIEWPORT_H = 1185
MATCH_MIN_SCORE = 0.08
NOMINAL_REGULARIZATION_WEIGHT = 0.04
YELLOW_BOUNDARY_MIN_PIXELS = 80
OUTSIDE_DARK_MIN_AREA = 1200
ORB_FEATURES = 3000
ORB_MIN_INLIERS = 12
ORB_RANSAC_THRESH = 4.0
ORB_MAX_SCALE_DRIFT = 0.03   # camera only pans — reject zoom-looking fits
ORB_MAX_ROTATION_DEG = 2.5   # ... and rotation-looking fits


@dataclass(frozen=True, slots=True)
class MatchEdge:
    i: int
    j: int
    dx: float
    dy: float
    score: float


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


def _yellow_boundary_mask(img: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # The kingdom edge marker is a pale yellow dashed line. Keep the range a
    # little broad because screenshots can be darkened by fog/edge overlays.
    return cv2.inRange(hsv, np.array((18, 35, 105)), np.array((42, 255, 255)))


def _valid_content_mask(img: np.ndarray) -> np.ndarray:
    yellow = _yellow_boundary_mask(img)
    if int(np.count_nonzero(yellow)) < YELLOW_BOUNDARY_MIN_PIXELS:
        return np.full(img.shape[:2], 255, dtype=np.uint8)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    dark = (gray < 95).astype(np.uint8) * 255
    dark = cv2.morphologyEx(
        dark,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)),
    )

    h, w = dark.shape
    flood = np.zeros((h + 2, w + 2), dtype=np.uint8)
    dark_for_fill = dark.copy()
    for x in range(w):
        if dark_for_fill[0, x]:
            cv2.floodFill(dark_for_fill, flood, (x, 0), 128)
        if dark_for_fill[h - 1, x]:
            cv2.floodFill(dark_for_fill, flood, (x, h - 1), 128)
    for y in range(h):
        if dark_for_fill[y, 0]:
            cv2.floodFill(dark_for_fill, flood, (0, y), 128)
        if dark_for_fill[y, w - 1]:
            cv2.floodFill(dark_for_fill, flood, (w - 1, y), 128)

    outside = (dark_for_fill == 128).astype(np.uint8) * 255
    if int(np.count_nonzero(outside)) < OUTSIDE_DARK_MIN_AREA:
        return np.full(img.shape[:2], 255, dtype=np.uint8)

    outside = cv2.morphologyEx(
        outside,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (23, 23)),
    )
    outside = cv2.dilate(outside, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    outside[yellow > 0] = 0
    mask = np.full(img.shape[:2], 255, dtype=np.uint8)
    mask[outside > 0] = 0
    return mask


def _feature_mask(
    img: np.ndarray,
    content_mask: np.ndarray | None,
    crop: dict | None,
) -> np.ndarray:
    """Where keypoints may live: the gesture-safe game area, on valid content.

    Frames are saved uncropped, so the HUD (top bar, bottom nav/chat, side
    buttons) is identical in every frame — features there would match with
    zero offset and drag the RANSAC fit toward "no movement". The crop rect
    from the scan config bounds detection to the world-content area.
    """
    h, w = img.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)
    if isinstance(crop, dict):
        x0 = max(0, int(crop.get("x") or 0))
        y0 = max(0, int(crop.get("y") or 0))
        x1 = min(w, x0 + int(crop.get("w") or w))
        y1 = min(h, y0 + int(crop.get("h") or h))
        if x1 > x0 and y1 > y0:
            mask[y0:y1, x0:x1] = 255
        else:
            mask[:] = 255
    else:
        mask[:] = 255
    if content_mask is not None:
        mask[content_mask == 0] = 0
    return mask


def _orb_features(
    img: np.ndarray, mask: np.ndarray,
) -> tuple[list[cv2.KeyPoint], np.ndarray | None]:
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    keypoints, descriptors = orb.detectAndCompute(
        cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), mask,
    )
    return list(keypoints), descriptors


def _orb_pair_offset(
    feat_a: tuple[list[cv2.KeyPoint], np.ndarray | None],
    feat_b: tuple[list[cv2.KeyPoint], np.ndarray | None],
) -> tuple[float, float, float] | None:
    """Translation ``pos_b - pos_a`` measured from matched keypoints.

    Needs no position prior: descriptors match globally, RANSAC rejects the
    outliers (animated icons, chat bubbles), and the similarity fit is then
    gated to a near-pure pan — the camera never rotates or zooms mid-scan.
    """
    kp_a, desc_a = feat_a
    kp_b, desc_b = feat_b
    if desc_a is None or desc_b is None:
        return None
    if len(kp_a) < ORB_MIN_INLIERS or len(kp_b) < ORB_MIN_INLIERS:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = sorted(bf.match(desc_b, desc_a), key=lambda m: m.distance)
    if len(matches) < ORB_MIN_INLIERS:
        return None
    src = np.float32([kp_b[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kp_a[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    # M maps b-points onto a-points; for the same world feature seen in both
    # frames, p_a - p_b == pos_b - pos_a, so M's translation IS the edge.
    M, inlier_mask = cv2.estimateAffinePartial2D(
        src, dst, method=cv2.RANSAC, ransacReprojThreshold=ORB_RANSAC_THRESH,
    )
    if M is None or inlier_mask is None:
        return None
    inliers = int(inlier_mask.sum())
    if inliers < ORB_MIN_INLIERS:
        return None
    scale = float(math.hypot(M[0, 0], M[1, 0]))
    angle = abs(math.degrees(math.atan2(M[1, 0], M[0, 0])))
    if abs(scale - 1.0) > ORB_MAX_SCALE_DRIFT or angle > ORB_MAX_ROTATION_DEG:
        return None
    score = inliers / len(matches)
    return float(M[0, 2]), float(M[1, 2]), float(score)


def _candidate_pairs(entries: list[dict]) -> list[tuple[int, int]]:
    """Pairs worth matching: consecutive in capture order + grid neighbors.

    Consecutive frames share the most overlap (one camera move apart); grid
    neighbors close loops across rows so drift cannot accumulate row by row.
    """
    pairs: set[tuple[int, int]] = set()
    for k in range(1, len(entries)):
        pairs.add((k - 1, k))
    by_idx = {(int(e["ix"]), int(e["iy"])): i for i, e in enumerate(entries)}
    for (ix, iy), i in by_idx.items():
        for dix, diy in ((1, 0), (0, 1)):
            j = by_idx.get((ix + dix, iy + diy))
            if j is not None:
                pairs.add((min(i, j), max(i, j)))
    return sorted(pairs)


def _find_match_edges(
    entries: list[dict],
    features: list[tuple[list[cv2.KeyPoint], np.ndarray | None] | None],
) -> list[MatchEdge]:
    edges: list[MatchEdge] = []
    for i, j in _candidate_pairs(entries):
        feat_a = features[i]
        feat_b = features[j]
        if feat_a is None or feat_b is None:
            continue
        cell_a = (entries[i].get("ix"), entries[i].get("iy"))
        cell_b = (entries[j].get("ix"), entries[j].get("iy"))
        estimate = _orb_pair_offset(feat_a, feat_b)
        if estimate is None:
            logger.info("stitch edge %s->%s: NO MATCH", cell_a, cell_b)
            continue
        dx, dy, score = estimate
        logger.info(
            "stitch edge %s->%s: dx=%.1f dy=%.1f score=%.2f", cell_a, cell_b, dx, dy, score,
        )
        edges.append(MatchEdge(i=i, j=j, dx=dx, dy=dy, score=score))
    logger.info("stitch edge matching: %d frame-pair matches", len(edges))
    return edges


def _grid_basis(
    entries: list[dict],
    edges: list[MatchEdge],
    fallback_right: tuple[float, float],
    fallback_down: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Screen-space vectors for one grid step right / down, from measured edges."""
    cells = [(int(e["ix"]), int(e["iy"])) for e in entries]
    right_offsets: list[tuple[float, float]] = []
    down_offsets: list[tuple[float, float]] = []
    for edge in edges:
        dix = cells[edge.j][0] - cells[edge.i][0]
        diy = cells[edge.j][1] - cells[edge.i][1]
        if (dix, diy) == (1, 0):
            right_offsets.append((edge.dx, edge.dy))
        elif (dix, diy) == (0, 1):
            down_offsets.append((edge.dx, edge.dy))

    def median_offset(
        bucket: list[tuple[float, float]], fallback: tuple[float, float],
    ) -> tuple[float, float]:
        if not bucket:
            return fallback
        xs = sorted(e[0] for e in bucket)
        ys = sorted(e[1] for e in bucket)
        return xs[len(xs) // 2], ys[len(ys) // 2]

    right = median_offset(right_offsets, fallback_right)
    down = median_offset(down_offsets, fallback_down)
    logger.info(
        "grid basis: right=(%.1f, %.1f) from %d pair(s), down=(%.1f, %.1f) from %d pair(s)",
        right[0], right[1], len(right_offsets), down[0], down[1], len(down_offsets),
    )
    return right, down


def _solve_matched_positions(
    nominal_positions: list[tuple[float, float]],
    images: list[np.ndarray | None],
    edges: list[MatchEdge],
) -> list[tuple[float, float]]:
    valid = [idx for idx, img in enumerate(images) if img is not None]
    if len(valid) < 2 or not edges:
        return nominal_positions

    index = {idx: row for row, idx in enumerate(valid)}
    rows: list[list[float]] = []
    bx: list[float] = []
    by: list[float] = []
    weights: list[float] = []

    for edge in edges:
        if edge.i not in index or edge.j not in index:
            continue
        row = [0.0] * len(valid)
        row[index[edge.j]] = 1.0
        row[index[edge.i]] = -1.0
        rows.append(row)
        bx.append(edge.dx)
        by.append(edge.dy)
        weights.append(max(edge.score, MATCH_MIN_SCORE))

    if not rows:
        return nominal_positions

    anchor = valid[0]
    anchor_row = [0.0] * len(valid)
    anchor_row[index[anchor]] = 1.0
    rows.append(anchor_row)
    bx.append(nominal_positions[anchor][0])
    by.append(nominal_positions[anchor][1])
    weights.append(4.0)

    for idx in valid:
        row = [0.0] * len(valid)
        row[index[idx]] = 1.0
        rows.append(row)
        bx.append(nominal_positions[idx][0])
        by.append(nominal_positions[idx][1])
        weights.append(NOMINAL_REGULARIZATION_WEIGHT)

    a = np.asarray(rows, dtype=np.float64)
    w = np.sqrt(np.asarray(weights, dtype=np.float64))[:, None]
    solved_x, *_ = np.linalg.lstsq(a * w, np.asarray(bx, dtype=np.float64) * w[:, 0], rcond=None)
    solved_y, *_ = np.linalg.lstsq(a * w, np.asarray(by, dtype=np.float64) * w[:, 0], rcond=None)

    positions = list(nominal_positions)
    for idx, row_idx in index.items():
        positions[idx] = (float(solved_x[row_idx]), float(solved_y[row_idx]))
    max_adjust = max(
        (
            ((positions[i][0] - nominal_positions[i][0]) ** 2 + (positions[i][1] - nominal_positions[i][1]) ** 2)
            ** 0.5
            for i in valid
        ),
        default=0.0,
    )
    logger.info("stitch edge matching: max position adjustment %.1f px", max_adjust)
    return positions


def run_stitch(run_dir: Path) -> Path:
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

    entries = list(manifest["frames"].values())
    if not entries:
        msg = f"{manifest_path} contains no frames"
        raise ValueError(msg)

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
        masks.append(_valid_content_mask(img))
    if missing:
        logger.warning("%d frame file(s) listed in the manifest are missing on disk", missing)
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
        if img is not None and mask is not None
        else None
        for img, mask in zip(images, masks, strict=True)
    ]
    edges = _find_match_edges(entries, features)
    right, down = _grid_basis(
        entries, edges, fallback_right=(step_x, 0.0), fallback_down=(0.0, step_y),
    )
    nominal_positions = [
        (
            entry["ix"] * right[0] + entry["iy"] * down[0],
            entry["ix"] * right[1] + entry["iy"] * down[1],
        )
        for entry in entries
    ]
    positions = _solve_matched_positions(nominal_positions, images, edges)

    # Frames are saved as-is (full screenshots, no UI crop), so the tile size
    # comes from each image instead of config geometry — capture and stitch
    # share one coordinate system.
    placed = [
        (img, mask, pos)
        for img, mask, pos in zip(images, masks, positions, strict=True)
        if img is not None and mask is not None
    ]
    off_x = min(px for _, _, (px, _) in placed)
    off_y = min(py for _, _, (_, py) in placed)
    canvas_w = max(int(round(px - off_x)) + img.shape[1] for img, _, (px, _) in placed)
    canvas_h = max(int(round(py - off_y)) + img.shape[0] for img, _, (_, py) in placed)
    logger.info("canvas %d×%d from %d frames", canvas_w, canvas_h, len(placed))
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    for img, mask, (px, py) in placed:
        x = int(round(px - off_x))
        y = int(round(py - off_y))
        h, w = img.shape[:2]
        roi = canvas[y : y + h, x : x + w]
        valid = mask > 0
        roi[valid] = img[valid]

    # Atomic writes: during a scan the live stitcher rewrites these every few
    # seconds while the API serves the preview — readers must never see a
    # half-written file.
    full_path = run_dir / MAP_FULL_NAME
    full_tmp = run_dir / f".{MAP_FULL_NAME}.tmp.png"
    if not cv2.imwrite(str(full_tmp), canvas):
        msg = f"failed to write {full_path}"
        raise RuntimeError(msg)
    full_tmp.replace(full_path)

    scale = PREVIEW_LONG_SIDE / max(canvas_w, canvas_h)
    preview = canvas
    if scale < 1.0:
        preview = cv2.resize(
            canvas,
            (int(canvas_w * scale), int(canvas_h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    preview_path = run_dir / MAP_PREVIEW_NAME
    preview_tmp = run_dir / f".{MAP_PREVIEW_NAME}.tmp.jpg"
    cv2.imwrite(str(preview_tmp), preview)
    preview_tmp.replace(preview_path)
    logger.info("stitched map saved: %s (+ %s)", full_path, preview_path)
    return full_path
