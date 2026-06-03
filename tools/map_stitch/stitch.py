#!/usr/bin/env python3
"""Stitch ./frames/frame_<row>_<col>.png into a single ./map_full.png.

The WoS map is isometric: buildings have height, so rooftops parallax-shift
more than the ground when the camera pans. We therefore estimate every
homography from the BOTTOM 33% of each frame only (ground tiles / roads — the
near-zero-parallax zone), then warp the FULL frame with that ground homography.
Building tops will ghost slightly in overlaps; per the spec that is accepted.

Pipeline:
  1. Load frames in grid order.
  2. ORB features on the ground band; BFMatcher (Hamming) pairwise matches.
  3. RANSAC homography for each grid-adjacent pair.
  4. BFS from the centre frame (anchor); chain homographies to anchor space.
  5. warpPerspective every frame onto one canvas; alpha-feather the overlaps.
  6. Crop to the bounding box of valid pixels; write map_full.png.

OpenCV only (no cv2.Stitcher).

    uv run python tools/map_stitch/stitch.py
"""

from __future__ import annotations

import argparse
import sys
from collections import deque
from pathlib import Path

import cv2
import numpy as np

# ======================= CONFIGURABLE PARAMETERS =============================
_BASE = Path(__file__).resolve().parent
FRAMES_DIR = _BASE / "frames"
OUTPUT_PATH = _BASE / "map_full.png"
GROUND_BAND = 0.33        # bottom fraction used for feature matching
ORB_FEATURES = 4000       # keypoint budget per frame
MIN_MATCH_COUNT = 12      # below this we treat the pair as unmatched
RANSAC_REPROJ_THRESH = 4.0
# ============================================================================


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
    print(f"Loaded {len(frames)} frames; detecting ORB features...", flush=True)

    orb = cv2.ORB_create(nfeatures=ORB_FEATURES)
    feats = {key: _detect(img, orb) for key, img in frames.items()}

    print("Estimating homographies (BFS from anchor)...", flush=True)
    H_to_anchor = _chain_to_anchor(frames, feats)

    print("Warping + blending onto canvas...", flush=True)
    translation, size = _canvas_bounds(frames, H_to_anchor)
    blended, valid = _blend(frames, H_to_anchor, translation, size)
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
