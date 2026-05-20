"""Lazy-loaded production digit classifier."""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from kNN.digital.classifier import DEFAULT_X0, DigitClassifier, DigitPrediction
from kNN.digital.paths import model_path

KNN_PREPROCESS_TAGS = frozenset({"knn", "digital"})


def is_knn_preprocess(tag: str | None) -> bool:
    return (tag or "").strip().lower() in KNN_PREPROCESS_TAGS


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


def recognize_digits(
    crop_bgr: np.ndarray,
    *,
    digit_count: int | None = None,
    x0: int = DEFAULT_X0,
) -> tuple[str, float]:
    """Return ``(text, mean_confidence)`` for a BGR digit strip crop."""
    pred = get_classifier().predict_strip(crop_bgr, digit_count=digit_count, x0=x0)
    return pred.text, pred.confidence


def recognize_digits_prediction(
    crop_bgr: np.ndarray,
    *,
    digit_count: int | None = None,
    x0: int = DEFAULT_X0,
) -> DigitPrediction:
    return get_classifier().predict_strip(crop_bgr, digit_count=digit_count, x0=x0)
