#!/usr/bin/env python3
"""Train ``kNN/digital`` model and evaluate on both labeled chief_profile strips.

    uv run python scripts/build_knn_digital_dataset.py
    uv run python scripts/train_knn_digital_model.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from kNN.digital import DigitClassifier, build_training_matrices, dataset_dir, model_path  # noqa: E402
from layout.area_lookup import screen_region_by_name  # noqa: E402
from layout.area_manifest import load_area_doc  # noqa: E402

WHO_I_AM_REFS = REPO / "games" / "wos" / "core" / "who_i_am" / "references" / "crop"

# (case_name, crop_path, expected_text, x0, area_bbox_region)
# ``area_bbox_region`` is the region name to bbox-crop the fixture by, or None
# when the path is already a pre-cropped digit strip.
EVAL_CASES: list[tuple[str, Path, str, int, str | None]] = [
    ("reference_id", WHO_I_AM_REFS / "chief_profile_player.id.png", "765502864", 4, None),
    ("reference_power", WHO_I_AM_REFS / "chief_profile_player.power.png", "17492", 0, None),
    ("reference_state", WHO_I_AM_REFS / "chief_profile_player.state.png", "4353", 0, None),
    (
        "live_id",
        REPO / "tests" / "fixtures" / "chief_profile_player_id_live.png",
        "401227964",
        0,
        "player.id",
    ),
]


def _crop_for_case(path: Path, *, area_bbox_region: str | None) -> object:
    if area_bbox_region is not None:
        area = load_area_doc(REPO)
        image = cv2.imread(str(path))
        if image is None:
            msg = f"missing fixture {path}"
            raise ValueError(msg)
        pair = screen_region_by_name(area, area_bbox_region)
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
    for name, path, expected, x0, region in EVAL_CASES:
        crop = _crop_for_case(path, area_bbox_region=region)
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
