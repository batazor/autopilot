#!/usr/bin/env python3
"""Stitch ./frames/frame_<row>_<col>.png into a single ./map_full.png.

The WoS world view is a controlled grid capture, not a free panorama: every
frame already has a logical (row, col). Feature-only stitching is fragile here
because the screenshots contain large static HUD regions and low-texture snow /
water. The stitcher therefore estimates the real screen-space grid basis from
overlap strips, masks the HUD for matching, and always lays out every captured
frame on one canvas. If overlap confidence is weak, it falls back to the
requested overlap geometry instead of dropping frames.

OpenCV only (no cv2.Stitcher).

    uv run python tools/map_stitch/stitch.py
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

# ======================= CONFIGURABLE PARAMETERS =============================
_BASE = Path(__file__).resolve().parent
FRAMES_DIR = _BASE / "frames"
OUTPUT_PATH = _BASE / "map_full.png"
GROUND_BAND = 0.33        # retained for CLI compatibility / old docs
ORB_FEATURES = 4000       # keypoint budget per frame
MIN_MATCH_COUNT = 12      # below this we treat the pair as unmatched
RANSAC_REPROJ_THRESH = 4.0
DEFAULT_OVERLAP = 0.30
MIN_TEMPLATE_SCORE = 0.12

# Crop to the actual map viewport. These percentages intentionally discard the
# top resource bar, bottom nav/chat, and most right-side floating buttons. The
# missing map edge is usually present in the neighbouring capture.
VIEW_TOP_FRAC = 0.09
VIEW_BOTTOM_FRAC = 0.25
VIEW_LEFT_FRAC = 0.00
VIEW_RIGHT_FRAC = 0.25
# ============================================================================


@dataclass(frozen=True)
class CropBox:
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0


@dataclass(frozen=True)
class TranslationEstimate:
    dx: float
    dy: float
    score: float


def _load_frames(frames_dir: Path) -> dict[tuple[int, int], np.ndarray]:
    """Read every frame_<r>_<c>.png into a {(row, col): BGR image} map."""
    frames: dict[tuple[int, int], np.ndarray] = {}
    for path in sorted(frames_dir.glob("frame_*.png")):
        stem = path.stem.split("_")  # ["frame", row, col]
        r, c = int(stem[1]), int(stem[2])
        img = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if img is None:
            print(f"WARN: unreadable {path.name}", file=sys.stderr)
            continue
        frames[(r, c)] = img
    return frames


def _viewport_crop(img: np.ndarray) -> CropBox:
    """Return the stable game-map crop, scaled to the current device size."""
    h, w = img.shape[:2]
    x0 = int(round(w * VIEW_LEFT_FRAC))
    y0 = int(round(h * VIEW_TOP_FRAC))
    x1 = int(round(w * (1.0 - VIEW_RIGHT_FRAC)))
    y1 = int(round(h * (1.0 - VIEW_BOTTOM_FRAC)))
    # Keep a usable viewport even on unusual aspect ratios.
    if x1 - x0 < w * 0.45:
        x0, x1 = 0, w
    if y1 - y0 < h * 0.45:
        y0, y1 = 0, h
    return CropBox(x0=x0, y0=y0, x1=x1, y1=y1)


def _crop_frame(img: np.ndarray, crop: CropBox) -> np.ndarray:
    return img[crop.y0:crop.y1, crop.x0:crop.x1]


def _match_ready(img: np.ndarray) -> np.ndarray:
    """Preprocess a cropped frame for template matching.

    High-pass grayscale reduces snow/water gradients and makes roads, cliffs,
    shorelines, and building edges dominate the correlation score.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    highpass = cv2.absdiff(gray, cv2.GaussianBlur(gray, (0, 0), 5))
    return cv2.equalizeHist(highpass)


def _template_translation(
    parent: np.ndarray,
    child: np.ndarray,
    *,
    direction: str,
    overlap: float,
) -> TranslationEstimate | None:
    """Estimate child->parent translation for one grid-adjacent pair.

    ``direction`` is the logical child direction: ``right`` for (r, c+1) and
    ``down`` for (r+1, c). The search is two-dimensional because in the
    isometric WoS map a horizontal/vertical drag can produce diagonal screen
    motion after inertia settles.
    """
    parent_p = _match_ready(parent)
    child_p = _match_ready(child)
    h, w = child_p.shape[:2]

    if direction == "right":
        tw = min(max(80, int(w * 0.28)), 180)
        th = min(max(260, int(h * 0.58)), 520)
        tx = 0
        ty = (h - th) // 2
        expected_x = int(w * (1.0 - overlap))
        expected_y = 0
    elif direction == "down":
        tw = min(max(240, int(w * 0.62)), 360)
        th = min(max(110, int(h * 0.28)), 240)
        tx = (w - tw) // 2
        ty = 0
        expected_x = 0
        expected_y = int(h * (1.0 - overlap))
    else:
        msg = f"unsupported direction: {direction}"
        raise ValueError(msg)

    if tw >= w or th >= h:
        return None

    margin_x = int(w * 0.45)
    margin_y = int(h * 0.45)
    sx0 = max(0, tx + expected_x - margin_x)
    sx1 = min(w - tw, tx + expected_x + margin_x)
    sy0 = max(0, ty + expected_y - margin_y)
    sy1 = min(h - th, ty + expected_y + margin_y)
    if sx1 <= sx0 or sy1 <= sy0:
        return None

    template = child_p[ty:ty + th, tx:tx + tw]
    if float(template.std()) < 2.0:
        return None
    target = parent_p[sy0:sy1 + th, sx0:sx1 + tw]
    result = cv2.matchTemplate(target, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    return TranslationEstimate(
        dx=float(sx0 + loc[0] - tx),
        dy=float(sy0 + loc[1] - ty),
        score=float(score),
    )


def _weighted_median(values: list[float], weights: list[float]) -> float:
    order = np.argsort(values)
    sorted_values = np.asarray(values, np.float64)[order]
    sorted_weights = np.asarray(weights, np.float64)[order]
    cutoff = sorted_weights.sum() * 0.5
    idx = int(np.searchsorted(np.cumsum(sorted_weights), cutoff, side="left"))
    return float(sorted_values[min(idx, len(sorted_values) - 1)])


def _basis_from_estimates(
    frames: dict[tuple[int, int], np.ndarray],
    cropped: dict[tuple[int, int], np.ndarray],
    *,
    overlap: float,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return ``(right_basis, down_basis)`` in cropped-frame pixels."""
    sample = next(iter(cropped.values()))
    h, w = sample.shape[:2]
    fallback_right = (w * (1.0 - overlap), 0.0)
    fallback_down = (0.0, h * (1.0 - overlap))

    right_estimates: list[TranslationEstimate] = []
    down_estimates: list[TranslationEstimate] = []
    for r, c in sorted(frames):
        if (r, c + 1) in frames:
            est = _template_translation(
                cropped[(r, c)], cropped[(r, c + 1)],
                direction="right", overlap=overlap,
            )
            if est and est.score >= MIN_TEMPLATE_SCORE:
                right_estimates.append(est)
        if (r + 1, c) in frames:
            est = _template_translation(
                cropped[(r, c)], cropped[(r + 1, c)],
                direction="down", overlap=overlap,
            )
            if est and est.score >= MIN_TEMPLATE_SCORE:
                down_estimates.append(est)

    def robust_basis(
        estimates: list[TranslationEstimate],
        fallback: tuple[float, float],
        *,
        axis: str,
    ) -> tuple[float, float]:
        if not estimates:
            return fallback
        if axis == "right":
            estimates = [
                e for e in estimates
                if 0.15 * w <= e.dx <= 0.90 * w and abs(e.dy) <= 0.60 * h
            ]
        else:
            estimates = [
                e for e in estimates
                if 0.30 * h <= e.dy <= 0.90 * h and abs(e.dx) <= 0.60 * w
            ]
        if not estimates:
            return fallback
        weights = [max(e.score, MIN_TEMPLATE_SCORE) ** 2 for e in estimates]
        dx = _weighted_median([e.dx for e in estimates], weights)
        dy = _weighted_median([e.dy for e in estimates], weights)
        return dx, dy

    right = robust_basis(right_estimates, fallback_right, axis="right")
    down = robust_basis(down_estimates, fallback_down, axis="down")
    print(
        "Grid basis: "
        f"right=({right[0]:.1f},{right[1]:.1f}) from {len(right_estimates)} edge(s); "
        f"down=({down[0]:.1f},{down[1]:.1f}) from {len(down_estimates)} edge(s)",
        flush=True,
    )
    return right, down


def _grid_positions(
    frames: dict[tuple[int, int], np.ndarray],
    right: tuple[float, float],
    down: tuple[float, float],
) -> dict[tuple[int, int], tuple[float, float]]:
    min_r = min(r for r, _ in frames)
    min_c = min(c for _, c in frames)
    return {
        (r, c): (
            (c - min_c) * right[0] + (r - min_r) * down[0],
            (c - min_c) * right[1] + (r - min_r) * down[1],
        )
        for r, c in frames
    }


def _detect(
    img: np.ndarray, orb: cv2.ORB,
) -> tuple[list[cv2.KeyPoint], np.ndarray | None, tuple[int, int]]:
    """ORB keypoints/descriptors over the FULL frame, plus its (h, w).

    We detect everywhere rather than only in the bottom band: the ground shared
    by a *vertical* pair lives at the TOP of the lower frame (the camera moved
    down), so a bottom-only crop would miss every vertical seam. Parallax from
    tall buildings is instead suppressed at match time — by selecting the
    low-parallax overlap band per pair (:func:`_select_band`) and by the RANSAC
    similarity fit that treats inconsistent rooftop points as outliers.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kps, desc = orb.detectAndCompute(gray, None)
    return list(kps), desc, img.shape[:2]


def _select_band(
    feats: tuple, region: str, band: float,
) -> tuple[list[cv2.KeyPoint], np.ndarray | None]:
    """Keep only keypoints/descriptors in one edge band of the frame.

    region ∈ {bottom, top, left, right, all}. Restricting each frame of a pair
    to the band where they actually overlap (a) cuts false matches and (b)
    biases toward the flat, low-parallax ground strip the spec calls for.
    """
    kps, desc, (h, w) = feats
    if desc is None:
        return [], None
    keep = []
    for i, kp in enumerate(kps):
        x, y = kp.pt
        if region == "bottom":
            ok = y >= (1.0 - band) * h
        elif region == "top":
            ok = y <= band * h
        elif region == "left":
            ok = x <= band * w
        elif region == "right":
            ok = x >= (1.0 - band) * w
        else:  # "all"
            ok = True
        if ok:
            keep.append(i)
    if len(keep) < MIN_MATCH_COUNT:
        return [], None
    return [kps[i] for i in keep], desc[keep]


def _pairwise_homography(
    feats_src: tuple, feats_dst: tuple, src_region: str, dst_region: str,
) -> np.ndarray | None:
    """3x3 transform mapping the SRC frame into the DST frame's pixel space.

    The camera only *pans* across the map (no real rotation/zoom), and the
    feature-bearing overlap is a thin strip. A full 8-DOF ``findHomography`` is
    under-constrained there: its perspective terms fit the strip but explode
    when the whole 720-wide frame is warped (canvas blows up ~10x). We therefore
    fit a 4-DOF similarity (translation + small rotation + scale) with RANSAC
    outlier rejection, returned in homogeneous 3x3 form so the rest of the
    pipeline is unchanged.
    """
    kp_s, desc_s = _select_band(feats_src, src_region, GROUND_BAND)
    kp_d, desc_d = _select_band(feats_dst, dst_region, GROUND_BAND)
    if desc_s is None or desc_d is None:
        return None
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    # bf.match returns a tuple on some OpenCV builds — sort into a new list.
    matches = sorted(bf.match(desc_s, desc_d), key=lambda m: m.distance)
    if len(matches) < MIN_MATCH_COUNT:
        return None
    src_pts = np.float32([kp_s[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp_d[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    M, mask = cv2.estimateAffinePartial2D(
        src_pts, dst_pts, method=cv2.RANSAC,
        ransacReprojThreshold=RANSAC_REPROJ_THRESH,
    )
    if M is None or mask is None or int(mask.sum()) < MIN_MATCH_COUNT:
        return None
    return np.vstack([M, [0.0, 0.0, 1.0]])  # 2x3 affine -> 3x3 homogeneous


def _overlap_regions(dr: int, dc: int) -> tuple[str, str]:
    """Pick which band of (child, parent) overlaps, given their grid offset.

    Returns (child_region, parent_region). Vertical pairs share the lower
    frame's TOP with the upper frame's BOTTOM; horizontal pairs share the
    low-parallax bottom strip of both.
    """
    if dr == 1:    # child is below parent
        return "top", "bottom"
    if dr == -1:   # child is above parent
        return "bottom", "top"
    # Horizontal pair: both share the low-parallax bottom ground strip.
    return "bottom", "bottom"


def _chain_to_anchor(
    frames: dict[tuple[int, int], np.ndarray],
    feats: dict[tuple[int, int], tuple],
) -> dict[tuple[int, int], np.ndarray]:
    """BFS from the centre frame, composing per-edge homographies to anchor space."""
    coords = list(frames)
    rows = [r for r, _ in coords]
    cols = [c for _, c in coords]
    anchor = (
        min(rows, key=lambda r: abs(r - (min(rows) + max(rows)) / 2)),
        min(cols, key=lambda c: abs(c - (min(cols) + max(cols)) / 2)),
    )
    H_to_anchor: dict[tuple[int, int], np.ndarray] = {anchor: np.eye(3)}
    seen = {anchor}
    q = deque([anchor])
    while q:
        r, c = q.popleft()
        for nr, nc in ((r, c + 1), (r, c - 1), (r + 1, c), (r - 1, c)):
            child = (nr, nc)
            if child not in frames or child in seen:
                continue
            # H mapping child -> parent, then parent -> anchor. Match the bands
            # where the two frames actually overlap (direction-dependent).
            src_region, dst_region = _overlap_regions(nr - r, nc - c)
            H_cp = _pairwise_homography(
                feats[child], feats[(r, c)], src_region, dst_region,
            )
            if H_cp is None:
                continue  # leave unmatched; BFS may reach it via another edge
            H_to_anchor[child] = H_to_anchor[(r, c)] @ H_cp
            seen.add(child)
            q.append(child)
    missing = set(frames) - set(H_to_anchor)
    if missing:
        print(f"WARN: {len(missing)} frame(s) could not be matched: "
              f"{sorted(missing)}", file=sys.stderr)
    return H_to_anchor


def _canvas_bounds(
    frames: dict[tuple[int, int], np.ndarray],
    H_to_anchor: dict[tuple[int, int], np.ndarray],
) -> tuple[np.ndarray, tuple[int, int]]:
    """Project every frame's corners to find the canvas extent + translation."""
    all_corners = []
    for key, H in H_to_anchor.items():
        h, w = frames[key].shape[:2]
        corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        all_corners.append(cv2.perspectiveTransform(corners, H))
    pts = np.concatenate(all_corners).reshape(-1, 2)
    x_min, y_min = np.floor(pts.min(axis=0)).astype(int)
    x_max, y_max = np.ceil(pts.max(axis=0)).astype(int)
    translation = np.array([[1, 0, -x_min], [0, 1, -y_min], [0, 0, 1]], np.float64)
    size = (int(x_max - x_min), int(y_max - y_min))  # (width, height)
    return translation, size


def _feather_weight(h: int, w: int) -> np.ndarray:
    """Per-pixel blend weight: 1 at the centre, →0 at the frame border.

    Distance-transform feathering makes seams in overlaps fade smoothly instead
    of showing a hard edge where one frame stops contributing.
    """
    mask = np.ones((h, w), np.uint8)
    mask[0, :] = mask[-1, :] = mask[:, 0] = mask[:, -1] = 0
    dist = cv2.distanceTransform(mask, cv2.DIST_L2, 3)
    if dist.max() > 0:
        dist /= dist.max()
    return dist + 1e-3  # keep strictly positive so lone-pixel regions survive


def _blend(
    frames: dict[tuple[int, int], np.ndarray],
    H_to_anchor: dict[tuple[int, int], np.ndarray],
    translation: np.ndarray,
    size: tuple[int, int],
) -> tuple[np.ndarray, np.ndarray]:
    """Accumulate weighted warps, then normalise — simple alpha feathering."""
    w, h = size
    acc = np.zeros((h, w, 3), np.float32)      # Σ image·weight
    wsum = np.zeros((h, w), np.float32)        # Σ weight
    for key, H in H_to_anchor.items():
        img = frames[key].astype(np.float32)
        fh, fw = img.shape[:2]
        full_H = translation @ H
        warped = cv2.warpPerspective(img, full_H, size)
        weight = cv2.warpPerspective(_feather_weight(fh, fw), full_H, size)
        weight = np.clip(weight, 0, None)
        acc += warped * weight[..., None]
        wsum += weight
    valid = wsum > 1e-6
    out = np.zeros_like(acc)
    out[valid] = acc[valid] / wsum[valid, None]
    return out.astype(np.uint8), valid


def _blend_grid(
    cropped: dict[tuple[int, int], np.ndarray],
    positions: dict[tuple[int, int], tuple[float, float]],
) -> tuple[np.ndarray, np.ndarray]:
    """Blend cropped frames onto a grid-positioned canvas."""
    sample = next(iter(cropped.values()))
    fh, fw = sample.shape[:2]
    rounded = {
        key: (int(round(x)), int(round(y)))
        for key, (x, y) in positions.items()
    }
    min_x = min(x for x, _ in rounded.values())
    min_y = min(y for _, y in rounded.values())
    max_x = max(x + fw for x, _ in rounded.values())
    max_y = max(y + fh for _, y in rounded.values())
    width = max_x - min_x
    height = max_y - min_y
    acc = np.zeros((height, width, 3), np.float32)
    wsum = np.zeros((height, width), np.float32)
    weight = _feather_weight(fh, fw)

    for key in sorted(cropped):
        img = cropped[key].astype(np.float32)
        x, y = rounded[key]
        x -= min_x
        y -= min_y
        acc[y:y + fh, x:x + fw] += img * weight[..., None]
        wsum[y:y + fh, x:x + fw] += weight

    valid = wsum > 1e-6
    out = np.zeros_like(acc)
    out[valid] = acc[valid] / wsum[valid, None]
    return out.astype(np.uint8), valid


def _crop_to_valid(img: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Crop to the bounding box of contributing (non-empty) pixels."""
    ys, xs = np.where(valid)
    if len(xs) == 0:
        return img
    return img[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]


def stitch(frames_dir: Path = FRAMES_DIR, output: Path = OUTPUT_PATH) -> Path:
    frames = _load_frames(frames_dir)
    if not frames:
        msg = f"no frames found in {frames_dir}"
        raise RuntimeError(msg)
    print(f"Loaded {len(frames)} frames; estimating grid mosaic...", flush=True)

    crop = _viewport_crop(next(iter(frames.values())))
    cropped = {key: _crop_frame(img, crop) for key, img in frames.items()}
    print(
        f"Using map viewport crop: x={crop.x0}:{crop.x1}, y={crop.y0}:{crop.y1}",
        flush=True,
    )

    right, down = _basis_from_estimates(
        frames, cropped, overlap=DEFAULT_OVERLAP,
    )
    positions = _grid_positions(frames, right, down)

    print("Blending grid frames onto canvas...", flush=True)
    blended, valid = _blend_grid(cropped, positions)
    result = _crop_to_valid(blended, valid)

    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(output), result)
    print(f"Stitch complete: {output} ({result.shape[1]}x{result.shape[0]})",
          flush=True)
    return output


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ORB/RANSAC map stitcher (no cv2.Stitcher)")
    p.add_argument("--frames-dir", type=Path, default=FRAMES_DIR)
    p.add_argument("--output", type=Path, default=OUTPUT_PATH)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        stitch(frames_dir=a.frames_dir, output=a.output)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
