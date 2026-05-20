#!/usr/bin/env python3
"""Build mini digit dataset for ``kNN/digital`` (chief_profile player.id).

Writes ``data/kNN/digital/dataset/{0-9}/*.png`` (20×32 grayscale glyphs).

    uv run python scripts/build_knn_digital_dataset.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from kNN.digital import (  # noqa: E402
    DIGIT_CELL_H,
    DIGIT_CELL_W,
    augment_glyph,
    dataset_dir,
    extract_labeled_glyphs,
    glyph_to_feature,
    render_synthetic_digit,
    save_dataset_meta,
)
from layout.area_lookup import screen_region_by_name  # noqa: E402

AREA_JSON = REPO / "area.json"

LABELED_STRIPS: list[tuple[Path, str, int]] = [
    (REPO / "references" / "crop" / "chief_profile_player.id.png", "765502864", 4),
    (REPO / "tests" / "fixtures" / "chief_profile_player_id_live.png", "401227964", 0),
]


def _save_glyph(path: Path, gray: np.ndarray) -> None:
    norm = glyph_to_feature(gray).reshape(DIGIT_CELL_H, DIGIT_CELL_W)
    out = (norm * 255.0).astype(np.uint8)
    cv2.imwrite(str(path), out)


def _write_samples(
    digit: str,
    gray: np.ndarray,
    *,
    tag: str,
    counters: dict[str, int],
) -> int:
    n = 0
    folder = dataset_dir() / digit
    folder.mkdir(parents=True, exist_ok=True)
    seed = hash((tag, digit)) % (2**32)
    for aug_i, aug in enumerate(augment_glyph(gray, seed=seed)):
        idx = counters[digit]
        counters[digit] = idx + 1
        _save_glyph(folder / f"{tag}_{idx:03d}_a{aug_i}.png", aug)
        n += 1
    return n


def _crop_from_fixture(fixture: Path, region_name: str = "player.id") -> object | None:
    area = json.loads(AREA_JSON.read_text(encoding="utf-8"))
    image = cv2.imread(str(fixture))
    if image is None:
        return None
    pair = screen_region_by_name(area, region_name)
    if pair is None:
        return None
    bbox = pair[1]["bbox"]
    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    return image[py : py + ph, px : px + pw].copy()


def main() -> int:
    root = dataset_dir()
    if root.exists():
        for old in root.glob("*/*.png"):
            old.unlink()
    else:
        root.mkdir(parents=True)

    counters: dict[str, int] = {str(d): 0 for d in range(10)}
    total = 0
    sources: list[dict[str, str | int]] = []

    for path, label, x0 in LABELED_STRIPS:
        if path.name.startswith("chief_profile_player_id_live"):
            crop = _crop_from_fixture(path)
            strip_path = path
        else:
            crop = cv2.imread(str(path))
            strip_path = path
        if crop is None:
            print(f"skip (unreadable): {path}", file=sys.stderr)
            continue
        try:
            glyphs = extract_labeled_glyphs(crop, label, x0=x0)
        except ValueError as exc:
            print(f"skip {path}: {exc}", file=sys.stderr)
            continue
        tag = strip_path.stem
        for ch, patch in glyphs:
            total += _write_samples(ch, patch, tag=tag, counters=counters)
        sources.append({"path": str(path.relative_to(REPO)), "label": label, "x0": x0})

    for digit in "0123456789":
        for i in range(6):
            scale = 0.45 + i * 0.04
            syn = render_synthetic_digit(digit, font_scale=scale)
            total += _write_samples(digit, syn, tag=f"syn_s{scale:.2f}", counters=counters)

    per_class = {d: counters[d] for d in counters}
    save_dataset_meta(
        root,
        {
            "cell_w": DIGIT_CELL_W,
            "cell_h": DIGIT_CELL_H,
            "sources": sources,
            "per_class_counts": per_class,
            "total_samples": total,
        },
    )
    print(f"dataset: {root}")
    print(f"per class: {per_class}")
    print(f"total: {total}")
    missing = [d for d, c in per_class.items() if c < 3]
    if missing:
        print(f"warning: sparse classes: {missing}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
