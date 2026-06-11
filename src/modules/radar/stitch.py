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

from modules.radar.border import yellow_boundary_mask
from modules.radar.geometry import Affine, Corners
from modules.radar.scanner import MANIFEST_NAME

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)

PREVIEW_LONG_SIDE = 4096
MAP_FULL_NAME = "map_full.png"
MAP_PREVIEW_NAME = "map_preview.jpg"
MAP_META_NAME = "map_meta.json"
DEFAULT_STITCH_VIEWPORT_W = 720
DEFAULT_STITCH_VIEWPORT_H = 1185
MATCH_MIN_SCORE = 0.08
NOMINAL_REGULARIZATION_WEIGHT = 0.04
YELLOW_BOUNDARY_MIN_PIXELS = 80
OUTSIDE_DARK_MIN_AREA = 1200
# A frame whose valid (inside-kingdom) share of the crop is below this is
# dropped entirely: almost-all-dark frames contribute no reliable features
# and only destabilize the position solve and the paste.
OUTSIDE_FRAME_MIN_VALID_FRAC = 0.25
# Solved positions vs measured pair offsets: residuals above this (px) mean a
# visible seam — reported in map meta and the log.
SEAM_WARN_PX = 8.0
ORB_FEATURES = 3000
ORB_MIN_INLIERS = 12
ORB_RANSAC_THRESH = 4.0
# Phase-correlation refinement of ORB edges: ORB keypoints are quantized to
# pixels and RANSAC averages them, leaving 1-3 px residuals that show up as
# visible seams. Phase correlation on the overlapping strip is sub-pixel.
PHASE_REFINE_MAX_PX = 12.0       # bigger residual = correlation locked elsewhere
PHASE_REFINE_MIN_RESPONSE = 0.05 # peak sharpness below this = untrustworthy
PHASE_REFINE_MIN_OVERLAP_PX = 96 # need a real strip to correlate on
ORB_MAX_SCALE_DRIFT = 0.03   # camera only pans — reject zoom-looking fits
ORB_MAX_ROTATION_DEG = 2.5   # ... and rotation-looking fits
# Navigation prior gate: the map is full of identical sprites and a diagonal
# iso grid, so an unconstrained consensus can lock onto a diagonally-shifted
# alias (right dx, phantom dy). Matches are pre-filtered to a window around
# the offset navigation says happened: the swipe vector ± fling inertia.
PRIOR_TOLERANCE_MIN_PX = 140.0
PRIOR_TOLERANCE_FRAC = 0.6


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


# Border detection (yellow dashed line) lives in modules.radar.border — the
# scanner positions against it too, so it is shared, not stitch-private.
_yellow_boundary_mask = yellow_boundary_mask


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

    # Only dark regions that actually touch the yellow kingdom border are
    # "outside". Regular map content can be dark too (mountains, cliffs,
    # terrain shadows — especially at the wrong zoom), and a golden event
    # marker elsewhere in the frame is enough to trip the yellow trigger,
    # so the trigger alone cannot be trusted to mean "border in frame".
    yellow_zone = cv2.dilate(
        yellow, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31)),
    )
    component_count, labels = cv2.connectedComponents((outside > 0).astype(np.uint8))
    near_border = np.zeros(img.shape[:2], dtype=np.uint8)
    for label in range(1, component_count):
        component = labels == label
        if np.any(yellow_zone[component] > 0):
            near_border[component] = 255
    if not near_border.any():
        return np.full(img.shape[:2], 255, dtype=np.uint8)

    near_border = cv2.morphologyEx(
        near_border,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (23, 23)),
    )
    near_border = cv2.dilate(
        near_border, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)),
    )
    near_border[yellow > 0] = 0
    mask = np.full(img.shape[:2], 255, dtype=np.uint8)
    mask[near_border > 0] = 0
    return mask


def _useful_area_mask(
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


def _frame_mostly_outside(
    img: np.ndarray,
    content_mask: np.ndarray,
    crop: dict | None,
) -> bool:
    """True when the valid (inside-kingdom) share of the crop is negligible.

    Such frames carry almost no matchable content — keeping them in the graph
    only destabilizes the position solve, and pasting them adds nothing.
    """
    in_crop = _useful_area_mask(img, None, crop)
    crop_area = int(np.count_nonzero(in_crop))
    in_crop[content_mask == 0] = 0
    return bool(
        crop_area
        and np.count_nonzero(in_crop) / crop_area < OUTSIDE_FRAME_MIN_VALID_FRAC,
    )


def _feature_mask(
    img: np.ndarray,
    content_mask: np.ndarray | None,
    crop: dict | None,
) -> np.ndarray:
    """Where ORB keypoints may live: the useful area MINUS the dashed border.

    The border dashes are identical and evenly spaced, so keypoints on them
    match one dash off and can drag the whole RANSAC consensus a full dash
    period along the line — visible as the yellow border misaligning between
    neighbouring frames. Excluded from feature detection only; the paste mask
    keeps the line on the stitched map.
    """
    mask = _useful_area_mask(img, content_mask, crop)
    yellow = _yellow_boundary_mask(img)
    if int(np.count_nonzero(yellow)) >= YELLOW_BOUNDARY_MIN_PIXELS:
        zone = cv2.dilate(
            yellow, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (13, 13)),
        )
        mask[zone > 0] = 0
    return mask


def _orb_features(
    img: np.ndarray, mask: np.ndarray,
) -> tuple[list[cv2.KeyPoint], np.ndarray | None]:
    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    keypoints, descriptors = orb.detectAndCompute(
        cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), mask,
    )
    return list(keypoints), descriptors


def _prior_tolerance(expected: tuple[float, float]) -> float:
    return max(PRIOR_TOLERANCE_MIN_PX, PRIOR_TOLERANCE_FRAC * math.hypot(*expected))


def _orb_pair_offset(
    feat_a: tuple[list[cv2.KeyPoint], np.ndarray | None],
    feat_b: tuple[list[cv2.KeyPoint], np.ndarray | None],
    expected: tuple[float, float] | None = None,
) -> tuple[float, float, float] | None:
    """Translation ``pos_b - pos_a`` measured from matched keypoints.

    With ``expected`` (the offset navigation believes happened) the match set
    is pre-filtered to displacements near it, so static-UI matches (zero
    displacement) and repeated-sprite aliases (phantom diagonal shifts) never
    reach the consensus. The RANSAC similarity fit is then gated to a
    near-pure pan — the camera never rotates or zooms mid-scan.
    """
    kp_a, desc_a = feat_a
    kp_b, desc_b = feat_b
    if desc_a is None or desc_b is None:
        return None
    if len(kp_a) < ORB_MIN_INLIERS or len(kp_b) < ORB_MIN_INLIERS:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = sorted(bf.match(desc_b, desc_a), key=lambda m: m.distance)
    if expected is not None:
        tol = _prior_tolerance(expected)
        matches = [
            m for m in matches
            if math.hypot(
                (kp_a[m.trainIdx].pt[0] - kp_b[m.queryIdx].pt[0]) - expected[0],
                (kp_a[m.trainIdx].pt[1] - kp_b[m.queryIdx].pt[1]) - expected[1],
            ) <= tol
        ]
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
    tx, ty = float(M[0, 2]), float(M[1, 2])
    if expected is not None and math.hypot(tx - expected[0], ty - expected[1]) > _prior_tolerance(expected):
        return None
    score = inliers / len(matches)
    return tx, ty, float(score)


def _seam_residuals(
    entries: list[dict],
    positions: list[tuple[float, float]],
    edges: list[MatchEdge],
) -> dict | None:
    """Solved positions vs measured pair offsets — the visible-seam report.

    The least-squares solve distributes inconsistencies between edges; a large
    residual on a pair means its frames are placed differently than the match
    measured — exactly what shows up as a stepped seam on the canvas. Goes
    into map meta so misalignments are visible in the report, not only by
    eyeballing tiles.
    """
    if not edges:
        return None
    scored = []
    for e in edges:
        pi, pj = positions[e.i], positions[e.j]
        residual = math.hypot(pj[0] - pi[0] - e.dx, pj[1] - pi[1] - e.dy)
        scored.append((residual, e))
    residuals = [r for r, _ in scored]
    cell = lambda k: f"{entries[k]['ix']:02d}_{entries[k]['iy']:02d}"  # noqa: E731
    worst = [
        {"cells": [cell(e.i), cell(e.j)], "residual_px": round(r, 2)}
        for r, e in sorted(scored, key=lambda t: t[0], reverse=True)[:5]
        if r > SEAM_WARN_PX
    ]
    report = {
        "edges": len(scored),
        "mean_px": round(float(np.mean(residuals)), 2),
        "max_px": round(float(np.max(residuals)), 2),
        "worst": worst,
    }
    if worst:
        logger.warning(
            "stitch: %d seam(s) exceed %.0f px (worst %.1f px at %s) — see map meta",
            len(worst), SEAM_WARN_PX, worst[0]["residual_px"], "-".join(worst[0]["cells"]),
        )
    return report


def _write_map_meta(
    run_dir: Path,
    cfg: dict,
    entries: list[dict],
    positions: list[tuple[float, float]],
    origin: tuple[float, float],
    right: tuple[float, float],
    down: tuple[float, float],
    seam: dict | None = None,
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

    path = run_dir / MAP_META_NAME
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    tmp.replace(path)
    logger.info("map meta saved: %s (anchor: %s)", path, "yes" if "anchor" in meta else "no")


def frames_consistent(
    prev: np.ndarray,
    cur: np.ndarray,
    crop: dict | None,
    expected: tuple[float, float] | None,
) -> tuple[float, float] | None:
    """Scanner-side view guard: the measured ``(dx, dy)`` when two consecutive
    captures register as a pure pan near the expected offset — i.e. same zoom,
    same screen, camera only moved — else None. Catches accidental zoom
    gestures mid-scan, and the measured offset feeds swipe auto-calibration."""
    feat_prev = _orb_features(prev, _feature_mask(prev, None, crop))
    feat_cur = _orb_features(cur, _feature_mask(cur, None, crop))
    estimate = _orb_pair_offset(feat_prev, feat_cur, expected=expected)
    return (estimate[0], estimate[1]) if estimate is not None else None


def move_prior(entry: dict) -> tuple[float, float] | None:
    """Expected ``pos_this - pos_previous`` from the swipes that led here.

    Dragging the finger by ``f`` moves the content by ``f``, so the same
    world point sits at ``p + f`` in the new frame → the frame-origin offset
    is ``-f``. Fling inertia only stretches it along the same direction,
    which the prior tolerance absorbs.
    """
    move = entry.get("move")
    if not isinstance(move, dict) or move.get("mode") != "swipe":
        return None
    swipes = move.get("swipes")
    if not isinstance(swipes, list) or not swipes:
        return None
    try:
        fx = sum(float(s["x2"]) - float(s["x1"]) for s in swipes)
        fy = sum(float(s["y2"]) - float(s["y1"]) for s in swipes)
    except (KeyError, TypeError, ValueError):
        return None
    return (-fx, -fy)


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


def _refine_offset_phase(
    img_a: np.ndarray,
    img_b: np.ndarray,
    dx: float,
    dy: float,
    crop: dict | None,
) -> tuple[float, float] | None:
    """Sub-pixel refinement of an ORB edge via phase correlation.

    The two frames are aligned by the ORB estimate and the overlapping strip is
    phase-correlated: the residual shift is the estimate's error. ORB keypoints
    are pixel-quantized, so its translations carry 1-3 px residuals that
    accumulate into visible seams; phase correlation is sub-pixel and uses the
    whole strip, not sparse corners. Returns the refined ``(dx, dy)`` or None
    when the strip is too small or the correlation peak is not trustworthy
    (then the ORB estimate stands).
    """
    if isinstance(crop, dict):
        cx, cy = max(0, int(crop.get("x") or 0)), max(0, int(crop.get("y") or 0))
        cw = int(crop.get("w") or img_a.shape[1])
        ch = int(crop.get("h") or img_a.shape[0])
        img_a = img_a[cy : cy + ch, cx : cx + cw]
        img_b = img_b[cy : cy + ch, cx : cx + cw]
    h, w = img_a.shape[:2]
    rdx, rdy = int(round(dx)), int(round(dy))
    x0, y0 = max(0, rdx), max(0, rdy)
    x1, y1 = min(w, w + rdx), min(h, h + rdy)
    if x1 - x0 < PHASE_REFINE_MIN_OVERLAP_PX or y1 - y0 < PHASE_REFINE_MIN_OVERLAP_PX:
        return None
    strip_a = cv2.cvtColor(img_a[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY).astype(np.float32)
    strip_b = cv2.cvtColor(
        img_b[y0 - rdy : y1 - rdy, x0 - rdx : x1 - rdx], cv2.COLOR_BGR2GRAY,
    ).astype(np.float32)
    window = cv2.createHanningWindow((strip_a.shape[1], strip_a.shape[0]), cv2.CV_32F)
    (sx, sy), response = cv2.phaseCorrelate(strip_a, strip_b, window)
    if response < PHASE_REFINE_MIN_RESPONSE or math.hypot(sx, sy) > PHASE_REFINE_MAX_PX:
        return None
    # strip_b is strip_a's content displaced by the estimate error e, and
    # phaseCorrelate(a, b) reports b's displacement as -e — subtract it.
    return rdx - sx, rdy - sy


def _match_pair(
    entries: list[dict],
    features: list[tuple[list[cv2.KeyPoint], np.ndarray | None] | None],
    images: list[np.ndarray | None],
    crop: dict | None,
    i: int,
    j: int,
    expected: tuple[float, float] | None,
) -> MatchEdge | None:
    feat_a = features[i]
    feat_b = features[j]
    if feat_a is None or feat_b is None:
        return None
    cell_a = (entries[i].get("ix"), entries[i].get("iy"))
    cell_b = (entries[j].get("ix"), entries[j].get("iy"))
    estimate = _orb_pair_offset(feat_a, feat_b, expected=expected)
    prior_label = (
        f" (prior {expected[0]:.0f},{expected[1]:.0f})" if expected is not None else ""
    )
    if estimate is None:
        logger.info("stitch edge %s->%s: NO MATCH%s", cell_a, cell_b, prior_label)
        return None
    dx, dy, score = estimate
    refined_label = ""
    img_a, img_b = images[i], images[j]
    if img_a is not None and img_b is not None:
        refined = _refine_offset_phase(img_a, img_b, dx, dy, crop)
        if refined is not None:
            refined_label = f" (orb {dx:.1f},{dy:.1f})"
            dx, dy = refined
    logger.info(
        "stitch edge %s->%s: dx=%.1f dy=%.1f score=%.2f%s%s",
        cell_a, cell_b, dx, dy, score, refined_label, prior_label,
    )
    return MatchEdge(i=i, j=j, dx=dx, dy=dy, score=score)


def _find_match_edges(
    entries: list[dict],
    features: list[tuple[list[cv2.KeyPoint], np.ndarray | None] | None],
    images: list[np.ndarray | None],
    crop: dict | None,
    fallback_right: tuple[float, float],
    fallback_down: tuple[float, float],
) -> tuple[list[MatchEdge], tuple[float, float], tuple[float, float]]:
    """Two-stage matching: consecutive pairs first (navigation prior from the
    actual swipes), then the remaining grid neighbors with the measured basis
    as their prior. Returns the edges plus the right/down basis vectors."""
    edges: list[MatchEdge] = []
    matched: set[tuple[int, int]] = set()
    for j in range(1, len(entries)):
        i = j - 1
        edge = _match_pair(entries, features, images, crop, i, j, move_prior(entries[j]))
        if edge is not None:
            edges.append(edge)
        matched.add((i, j))

    right, down = _grid_basis(entries, edges, fallback_right, fallback_down)

    cells = [(int(e["ix"]), int(e["iy"])) for e in entries]
    for i, j in _candidate_pairs(entries):
        if (i, j) in matched:
            continue
        dix = cells[j][0] - cells[i][0]
        diy = cells[j][1] - cells[i][1]
        expected = (
            dix * right[0] + diy * down[0],
            dix * right[1] + diy * down[1],
        )
        edge = _match_pair(entries, features, images, crop, i, j, expected)
        if edge is not None:
            edges.append(edge)
    # Final basis over ALL measured edges (stage 2 usually adds the first
    # true down-pairs) so the nominal layout regularizes toward measured
    # geometry instead of the axis-aligned fallback.
    right, down = _grid_basis(entries, edges, right, down)
    logger.info("stitch edge matching: %d frame-pair matches", len(edges))
    return edges, right, down


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
    nominal_positions = [
        (
            entry["ix"] * right[0] + entry["iy"] * down[0],
            entry["ix"] * right[1] + entry["iy"] * down[1],
        )
        for entry in entries
    ]
    positions = _solve_matched_positions(nominal_positions, images, edges)
    seam_report = _seam_residuals(entries, positions, edges)

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
    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)
    painted = np.zeros((canvas_h, canvas_w), dtype=bool)
    for img, paste_mask, (px, py) in placed:
        x = int(round(px - off_x))
        y = int(round(py - off_y))
        h, w = img.shape[:2]
        roi = canvas[y : y + h, x : x + w]
        valid = paste_mask > 0
        roi[valid] = img[valid]
        painted[y : y + h, x : x + w] |= valid

    ys, xs = np.where(painted)
    trim_x, trim_y = (int(xs.min()), int(ys.min())) if len(xs) else (0, 0)
    if len(xs):
        canvas = canvas[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    canvas_h, canvas_w = canvas.shape[:2]

    try:
        _write_map_meta(
            run_dir, cfg, entries, positions,
            origin=(off_x + trim_x, off_y + trim_y), right=right, down=down,
            seam=seam_report,
        )
    except Exception:
        logger.exception("radar: map_meta.json not written (georeference failed)")

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
