#!/usr/bin/env python3
"""Compare pHash / hybrid scoring variants for text.welcome_back on a fixture frame."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np

if TYPE_CHECKING:
    from collections.abc import Callable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from layout.template_match import (  # noqa: E402
    _PHASH_BITS,
    _color_similarity_score,
    _edge_similarity_score,
    _hamming64,
    _phash64,
    _phash_match_score,
    match_crop_1to1_at_bbox_percent,
    patch_bgr_from_bbox_percent,
)

FRAME = REPO / "tests/fixtures/bs1_current_state.png"
TEMPLATE = (
    REPO
    / "games/wos/core/welcome_back/references/crop/welcome_back_text.welcome_back.png"
)
BBOX = {
    "x": 35.13899613899614,
    "y": 18.515217391304347,
    "width": 29.44401544401545,
    "height": 2.1869565217391305,
}
THRESHOLD = 0.9


def score_from_hamming(h: int) -> float:
    return max(0.0, min(1.0, 1.0 - h / float(_PHASH_BITS)))


def phash64_custom(gray: np.ndarray) -> int:
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(np.float32(small))
    block = dct[:8, :8].copy()
    block[0, 0] = 0
    med = float(np.median(block))
    bits = (block >= med).astype(np.uint8).reshape(-1)
    out = 0
    for bit in bits:
        out = (out << 1) | int(bit)
    return out


def phash_score_custom(
    patch_bgr: np.ndarray,
    tpl_bgr: np.ndarray,
    *,
    prep: Callable[[np.ndarray], np.ndarray],
) -> tuple[float, int]:
    def gray(img: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        return prep(g)

    h = _hamming64(phash64_custom(gray(patch_bgr)), phash64_custom(gray(tpl_bgr)))
    return score_from_hamming(h), h


def prep_none(g: np.ndarray) -> np.ndarray:
    return g


def prep_blur3(g: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(g, (3, 3), 0)


def prep_blur5(g: np.ndarray) -> np.ndarray:
    return cv2.GaussianBlur(g, (5, 5), 0)


def prep_clahe(g: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
    return clahe.apply(g)


def prep_blur_clahe(g: np.ndarray) -> np.ndarray:
    return prep_clahe(prep_blur3(g))


def prep_normalize(g: np.ndarray) -> np.ndarray:
    g = g.astype(np.float32)
    g -= float(g.mean())
    std = float(g.std())
    if std > 1e-6:
        g /= std
    return np.clip(g * 32 + 128, 0, 255).astype(np.uint8)


def bgr_from_gray(g: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(g, cv2.COLOR_GRAY2BGR)


def phash_on_edges(patch_bgr: np.ndarray, tpl_bgr: np.ndarray) -> tuple[float, int]:
    def edges(img: np.ndarray) -> np.ndarray:
        g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        e = cv2.Canny(g, 50, 150)
        return cv2.cvtColor(e, cv2.COLOR_GRAY2BGR)

    h = _hamming64(_phash64(edges(patch_bgr)), _phash64(edges(tpl_bgr)))
    return score_from_hamming(h), h


def ncc_at_patch(patch: np.ndarray, tpl: np.ndarray) -> float:
    pg = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY)
    tg = cv2.cvtColor(tpl, cv2.COLOR_BGR2GRAY)
    return float(cv2.matchTemplate(pg, tg, cv2.TM_CCOEFF_NORMED)[0, 0])


def hybrid_matched(phash_s: float, ncc_s: float, thr: float) -> bool:
    return min(phash_s, ncc_s) >= thr


def main() -> None:
    frame = cv2.imread(str(FRAME))
    tpl = cv2.imread(str(TEMPLATE))
    if frame is None or tpl is None:
        msg = "failed to load images"
        raise SystemExit(msg)

    patch, origin = patch_bgr_from_bbox_percent(frame, BBOX)
    res_1to1 = match_crop_1to1_at_bbox_percent(frame, tpl, BBOX)

    phash_b, ham_b = _phash_match_score(patch, tpl)
    ncc = ncc_at_patch(patch, tpl)
    color = _color_similarity_score(patch, tpl)
    edge = _edge_similarity_score(patch, tpl)

    variants: list[tuple[str, float, int]] = [
        ("baseline (production _phash64)", phash_b, ham_b),
    ]
    for name, prep in [
        ("blur 3x3", prep_blur3),
        ("blur 5x5", prep_blur5),
        ("CLAHE", prep_clahe),
        ("blur + CLAHE", prep_blur_clahe),
        ("mean/std normalize", prep_normalize),
    ]:
        s, h = phash_score_custom(patch, tpl, prep=prep)
        variants.append((f"pHash + {name}", s, h))

    es, eh = phash_on_edges(patch, tpl)
    variants.append(("pHash on Canny edges", es, eh))

    composites = [
        ("max(phash, edge)", max(phash_b, edge)),
        ("max(phash, ncc)", max(phash_b, ncc)),
        ("max(phash, color)", max(phash_b, color)),
        ("0.6*phash + 0.4*edge", 0.6 * phash_b + 0.4 * edge),
        ("min(phash,ncc) hybrid gate", min(phash_b, ncc)),
    ]

    print(f"Frame: {FRAME.name} {frame.shape[1]}x{frame.shape[0]}")
    print(f"Template: {TEMPLATE.name} {tpl.shape[1]}x{tpl.shape[0]}")
    print(f"Patch @ {origin}: {patch.shape[1]}x{patch.shape[0]}")
    print(f"Threshold: {THRESHOLD}")
    print()
    print("--- Production path ---")
    print(f"match_crop_1to1 score: {res_1to1['score']:.4f} hamming={res_1to1.get('hash_distance')}")
    print(f"NCC @ bbox:            {ncc:.4f}")
    print(f"color:                 {color:.4f}")
    print(f"edge:                  {edge:.4f}")
    print(
        f"hybrid matched (min phash,ncc): {hybrid_matched(phash_b, ncc, THRESHOLD)} "
        f"({min(phash_b, ncc):.4f})"
    )
    print()
    print("--- pHash variants (patch vs template) ---")
    print(f"{'method':<28} {'score':>7} {'ham':>4} {'>=0.9':>6}")
    for name, s, h in variants:
        ok = "YES" if s >= THRESHOLD else "no"
        print(f"{name:<28} {s:7.4f} {h:4d} {ok:>6}")

    print()
    print("--- Composite scores (no re-hash) ---")
    print(f"{'method':<28} {'score':>7} {'>=0.9':>6}")
    for name, s in composites:
        ok = "YES" if s >= THRESHOLD else "no"
        print(f"{name:<28} {s:7.4f} {ok:>6}")

    # Sub-pixel shift sensitivity (±1px vertical — title bar alignment)
    print()
    print("--- Shift sensitivity (baseline pHash) ---")
    for dy in (-2, -1, 0, 1, 2):
        bbox = dict(BBOX)
        bbox["y"] = BBOX["y"] + (dy / 1280.0 * 100.0)
        try:
            p2, _ = patch_bgr_from_bbox_percent(frame, bbox)
            if p2.shape != tpl.shape:
                print(f"  dy={dy:+d}px: shape mismatch {p2.shape} vs {tpl.shape}")
                continue
            s2, h2 = _phash_match_score(p2, tpl)
            print(f"  dy={dy:+d}px: score={s2:.4f} hamming={h2}")
        except ValueError as e:
            print(f"  dy={dy:+d}px: {e}")


if __name__ == "__main__":
    main()
