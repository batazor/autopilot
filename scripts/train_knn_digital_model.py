#!/usr/bin/env python3
"""Train ``kNN/digital`` model and evaluate on both labeled chief_profile strips.

    uv run python scripts/build_knn_digital_dataset.py
    uv run python scripts/train_knn_digital_model.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from kNN.digital import DigitClassifier, build_training_matrices, dataset_dir, model_path  # noqa: E402
from layout.area_lookup import screen_region_by_name  # noqa: E402

AREA_JSON = REPO / "area.json"

EVAL_CASES: list[tuple[str, Path, str, int, bool]] = [
    (
        "reference",
        REPO / "references" / "crop" / "chief_profile_player.id.png",
        "765502864",
        4,
        False,
    ),
    (
        "live",
        REPO / "tests" / "fixtures" / "chief_profile_player_id_live.png",
        "401227964",
        0,
        True,
    ),
]


def _crop_for_case(path: Path, *, use_area_bbox: bool) -> object:
    if use_area_bbox:
        area = json.loads(AREA_JSON.read_text(encoding="utf-8"))
        image = cv2.imread(str(path))
        if image is None:
            msg = f"missing fixture {path}"
            raise ValueError(msg)
        pair = screen_region_by_name(area, "player.id")
        assert pair is not None
        bbox = pair[1]["bbox"]
        h, w = image.shape[:2]
        px = int(round(float(bbox["x"]) / 100.0 * w))
        py = int(round(float(bbox["y"]) / 100.0 * h))
        pw = int(round(float(bbox["width"]) / 100.0 * w))
        ph = int(round(float(bbox["height"]) / 100.0 * h))
        return image[py : py + ph, px : px + pw].copy()
    crop = cv2.imread(str(path))
    if crop is None:
        msg = f"missing crop {path}"
        raise ValueError(msg)
    return crop


def main() -> int:
    ds = dataset_dir()
    if not ds.is_dir():
        print("run build_knn_digital_dataset.py first", file=sys.stderr)
        return 1

    features, labels = build_training_matrices(ds)
    clf = DigitClassifier.train_from_samples(features, labels, k=3)
    out = model_path()
    clf.save(out)
    print(f"trained: {out}  samples={features.shape[0]}  dim={features.shape[1]}")

    all_ok = True
    for name, path, expected, x0, use_bbox in EVAL_CASES:
        crop = _crop_for_case(path, use_area_bbox=use_bbox)
        pred = clf.predict_strip(crop, digit_count=len(expected), x0=x0)
        ok = pred.text == expected
        all_ok = all_ok and ok
        print(
            f"{name} eval ({path.name}): text={pred.text!r} expected={expected!r} "
            f"conf={pred.confidence:.3f} "
            f"per_digit={[round(c, 3) for c in pred.per_digit_conf]} "
            f"ok={'yes' if ok else 'no'}"
        )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
