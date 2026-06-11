"""Assemble scanned frames into one canvas: ``map_full.png`` + a preview."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

import cv2
import numpy as np

from modules.radar.scanner import MANIFEST_NAME

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

PREVIEW_LONG_SIDE = 4096
DEFAULT_STITCH_VIEWPORT_W = 720
DEFAULT_STITCH_VIEWPORT_H = 1185
MATCH_SEARCH_PX = 140
MATCH_MAX_TEMPLATE_PX = 420
MATCH_MIN_TEMPLATE_PX = 72
MATCH_MIN_SCORE = 0.08
NOMINAL_REGULARIZATION_WEIGHT = 0.04
YELLOW_BOUNDARY_MIN_PIXELS = 80
OUTSIDE_DARK_MIN_AREA = 1200


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


def _match_image(img: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    cv2.normalize(mag, mag, 0.0, 1.0, cv2.NORM_MINMAX)
    return mag


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


def _prepare_for_matching(img: np.ndarray, mask: np.ndarray) -> np.ndarray:
    if np.all(mask):
        return img
    prepared = img.copy()
    valid = mask > 0
    fill = (
        np.median(prepared[valid], axis=0).astype(np.uint8)
        if np.any(valid)
        else np.array((0, 0, 0), dtype=np.uint8)
    )
    prepared[~valid] = fill
    return prepared


def _overlap_rect(w: int, h: int, dx: int, dy: int) -> tuple[int, int, int, int] | None:
    x0 = max(0, dx)
    y0 = max(0, dy)
    x1 = min(w, dx + w)
    y1 = min(h, dy + h)
    if x1 - x0 < MATCH_MIN_TEMPLATE_PX or y1 - y0 < MATCH_MIN_TEMPLATE_PX:
        return None
    return x0, y0, x1, y1


def _centered_template_rect(rect: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = rect
    ow = x1 - x0
    oh = y1 - y0
    tw = min(ow, MATCH_MAX_TEMPLATE_PX)
    th = min(oh, MATCH_MAX_TEMPLATE_PX)
    tx0 = x0 + (ow - tw) // 2
    ty0 = y0 + (oh - th) // 2
    return tx0, ty0, tx0 + tw, ty0 + th


def _estimate_pair_offset(
    a: np.ndarray,
    b: np.ndarray,
    expected_dx: float,
    expected_dy: float,
) -> tuple[float, float, float] | None:
    h, w = a.shape[:2]
    dx0 = int(round(expected_dx))
    dy0 = int(round(expected_dy))
    overlap = _overlap_rect(w, h, dx0, dy0)
    if overlap is None:
        return None

    ax0, ay0, ax1, ay1 = _centered_template_rect(overlap)
    patch_a = a[ay0:ay1, ax0:ax1]
    patch_b = b[ay0 - dy0 : ay1 - dy0, ax0 - dx0 : ax1 - dx0]
    if patch_a.shape != patch_b.shape or patch_a.size == 0:
        return None
    if float(np.std(patch_a)) < 0.01 or float(np.std(patch_b)) < 0.01:
        return None

    window = cv2.createHanningWindow((patch_a.shape[1], patch_a.shape[0]), cv2.CV_32F)
    shift, score = cv2.phaseCorrelate(patch_a, patch_b, window)
    shift_x, shift_y = float(shift[0]), float(shift[1])
    if (
        score < MATCH_MIN_SCORE
        or abs(shift_x) > MATCH_SEARCH_PX
        or abs(shift_y) > MATCH_SEARCH_PX
    ):
        return None

    actual_dx = expected_dx - shift_x
    actual_dy = expected_dy - shift_y
    return float(actual_dx), float(actual_dy), float(score)


def _find_match_edges(
    images: list[np.ndarray | None],
    masks: list[np.ndarray | None],
    nominal_positions: list[tuple[float, float]],
) -> list[MatchEdge]:
    prepared = [
        _match_image(_prepare_for_matching(img, mask))
        if img is not None and mask is not None
        else None
        for img, mask in zip(images, masks, strict=True)
    ]
    edges: list[MatchEdge] = []
    for i, img_a in enumerate(prepared):
        if img_a is None:
            continue
        ax, ay = nominal_positions[i]
        for j in range(i + 1, len(prepared)):
            img_b = prepared[j]
            if img_b is None:
                continue
            bx, by = nominal_positions[j]
            expected_dx = bx - ax
            expected_dy = by - ay
            h, w = img_a.shape[:2]
            if _overlap_rect(w, h, int(round(expected_dx)), int(round(expected_dy))) is None:
                continue
            estimate = _estimate_pair_offset(img_a, img_b, expected_dx, expected_dy)
            if estimate is None:
                continue
            dx, dy, score = estimate
            edges.append(MatchEdge(i=i, j=j, dx=dx, dy=dy, score=score))
    logger.info("stitch edge matching: %d frame-pair matches", len(edges))
    return edges


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
    crop_w = int(cfg["crop"]["w"])
    crop_h = int(cfg["crop"]["h"])
    overlap = float(cfg["overlap"])
    capture_w, capture_h = _capture_size(manifest, cfg)
    step_x = capture_w * (1.0 - overlap)
    step_y = capture_h * (1.0 - overlap)

    entries = list(manifest["frames"].values())
    if not entries:
        msg = f"{manifest_path} contains no frames"
        raise ValueError(msg)

    nominal_positions: list[tuple[float, float]] = []
    images: list[np.ndarray | None] = []
    masks: list[np.ndarray | None] = []
    missing = 0
    for entry in entries:
        nominal = (entry["ix"] * step_x, entry["iy"] * step_y)
        img = cv2.imread(str(run_dir / entry["file"]))
        if img is None:
            missing += 1
            images.append(None)
            masks.append(None)
            nominal_positions.append(nominal)
            continue
        images.append(img)
        masks.append(_valid_content_mask(img))
        nominal_positions.append(nominal)
    if missing:
        logger.warning("%d frame file(s) listed in the manifest are missing on disk", missing)
    positions = _solve_matched_positions(
        nominal_positions,
        images,
        _find_match_edges(images, masks, nominal_positions),
    )

    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    off_x, off_y = min(xs), min(ys)
    canvas_w = int(round(max(xs) - off_x)) + crop_w
    canvas_h = int(round(max(ys) - off_y)) + crop_h
    logger.info("canvas %d×%d from %d frames", canvas_w, canvas_h, len(entries) - missing)
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    for img, mask, (px, py) in zip(images, masks, positions, strict=True):
        if img is None or mask is None:
            continue
        x = int(round(px - off_x))
        y = int(round(py - off_y))
        roi = canvas[y : y + crop_h, x : x + crop_w]
        valid = mask > 0
        roi[valid] = img[valid]

    full_path = run_dir / "map_full.png"
    if not cv2.imwrite(str(full_path), canvas):
        msg = f"failed to write {full_path}"
        raise RuntimeError(msg)

    scale = PREVIEW_LONG_SIDE / max(canvas_w, canvas_h)
    preview = canvas
    if scale < 1.0:
        preview = cv2.resize(
            canvas,
            (int(canvas_w * scale), int(canvas_h * scale)),
            interpolation=cv2.INTER_AREA,
        )
    preview_path = run_dir / "map_preview.jpg"
    cv2.imwrite(str(preview_path), preview)
    logger.info("stitched map saved: %s (+ %s)", full_path, preview_path)
    return full_path
