"""Lazy-loaded production digit classifier."""
from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import cv2

from config.paths import repo_root
from kNN.digital.classifier import (
    DEFAULT_X0,
    CompactNumberPrediction,
    DigitClassifier,
    DigitPrediction,
    DigitTemplatePrediction,
    TemplateDigitClassifier,
    extract_labeled_compact_glyphs,
)
from kNN.digital.paths import dataset_dir, model_path
from layout.area_lookup import screen_region_by_name
from layout.area_manifest import load_area_doc

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

KNN_PREPROCESS_TAGS = frozenset({"knn", "digital"})
COMPACT_NUMBER_PREPROCESS_TAGS = frozenset({"compact", "compact_number", "compact-number"})

_COMPACT_POWER_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("tests/fixtures/chief_profile_player_id_live.png", "player.power", "41.8M"),
    ("tests/fixtures/chief_profile_player_id_live_2.png", "player.power", "113,462"),
)


def is_knn_preprocess(tag: str | None) -> bool:
    return (tag or "").strip().lower() in KNN_PREPROCESS_TAGS


def is_compact_number_preprocess(tag: str | None) -> bool:
    return (tag or "").strip().lower() in COMPACT_NUMBER_PREPROCESS_TAGS


@lru_cache(maxsize=1)
def get_classifier() -> DigitClassifier:
    path = model_path()
    if not path.is_file():
        msg = (
            f"kNN digit model missing: {path}. "
            "Run: uv run python scripts/build_knn_digital_dataset.py && "
            "uv run python scripts/train_knn_digital_model.py"
        )
        raise FileNotFoundError(msg)
    return DigitClassifier.load(path)


@lru_cache(maxsize=1)
def get_template_classifier() -> TemplateDigitClassifier:
    root = dataset_dir()
    if not root.is_dir():
        msg = (
            f"kNN digit dataset missing: {root}. "
            "Run: uv run python scripts/build_knn_digital_dataset.py"
        )
        raise FileNotFoundError(msg)
    return TemplateDigitClassifier.from_dataset(root)


def _crop_region_from_repo_image(root: Path, rel_path: str, region_name: str) -> object | None:
    image = cv2.imread(str(root / rel_path))
    if image is None:
        return None
    pair = screen_region_by_name(load_area_doc(root), region_name)
    if pair is None:
        return None
    bbox = pair[1]["bbox"]
    h, w = image.shape[:2]
    px = int(round(float(bbox["x"]) / 100.0 * w))
    py = int(round(float(bbox["y"]) / 100.0 * h))
    pw = int(round(float(bbox["width"]) / 100.0 * w))
    ph = int(round(float(bbox["height"]) / 100.0 * h))
    return image[py : py + ph, px : px + pw].copy()


@lru_cache(maxsize=1)
def get_compact_number_classifier() -> TemplateDigitClassifier:
    root = repo_root()
    templates = []
    templates.extend(
        (digit, mask)
        for digit, mask in zip(
            get_template_classifier()._labels,
            get_template_classifier()._masks,
            strict=True,
        )
    )
    for rel_path, region_name, label in _COMPACT_POWER_SOURCES:
        crop = _crop_region_from_repo_image(root, rel_path, region_name)
        if crop is None:
            continue
        for ch, glyph in extract_labeled_compact_glyphs(crop, label):
            from kNN.digital.classifier import glyph_to_mask

            templates.append((ch, glyph_to_mask(glyph)))
    return TemplateDigitClassifier(templates)


def recognize_digits(
    crop_bgr: np.ndarray,
    *,
    digit_count: int | None = None,
    x0: int = DEFAULT_X0,
) -> tuple[str, float]:
    """Return ``(text, mean_confidence)`` for a BGR digit strip crop."""
    pred = get_classifier().predict_strip(crop_bgr, digit_count=digit_count, x0=x0)
    return pred.text, pred.confidence


def recognize_digits_template(
    crop_bgr: np.ndarray,
    *,
    digit_count: int | None = None,
    x0: int = DEFAULT_X0,
) -> tuple[str, float]:
    """Return ``(text, mean_confidence)`` using nearest binary glyph templates."""
    pred = get_template_classifier().predict_strip(
        crop_bgr,
        digit_count=digit_count,
        x0=x0,
    )
    return pred.text, pred.confidence


def recognize_digits_prediction(
    crop_bgr: np.ndarray,
    *,
    digit_count: int | None = None,
    x0: int = DEFAULT_X0,
) -> DigitPrediction:
    return get_classifier().predict_strip(crop_bgr, digit_count=digit_count, x0=x0)


def recognize_digits_template_prediction(
    crop_bgr: np.ndarray,
    *,
    digit_count: int | None = None,
    x0: int = DEFAULT_X0,
) -> DigitTemplatePrediction:
    return get_template_classifier().predict_strip(crop_bgr, digit_count=digit_count, x0=x0)


def recognize_compact_number_prediction(
    crop_bgr: np.ndarray,
    *,
    x0: int = DEFAULT_X0,
) -> CompactNumberPrediction:
    return get_compact_number_classifier().predict_compact_number(crop_bgr, x0=x0)


def recognize_compact_number(
    crop_bgr: np.ndarray,
    *,
    x0: int = DEFAULT_X0,
) -> tuple[str, float]:
    pred = recognize_compact_number_prediction(crop_bgr, x0=x0)
    return pred.value_text, pred.confidence
