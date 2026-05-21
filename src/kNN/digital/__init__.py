"""Digit-strip kNN OCR (``cv2.ml.KNearest``)."""
from __future__ import annotations

from kNN.digital.classifier import (
    DEFAULT_DIGIT_COUNT,
    DEFAULT_X0,
    DigitClassifier,
    DigitPrediction,
    augment_glyph,
    build_training_matrices,
    estimate_digit_count,
    extract_labeled_glyphs,
    glyph_to_feature,
    parse_digit_count,
    render_synthetic_digit,
    save_dataset_meta,
    segment_digit_boxes,
)
from kNN.digital.loader import (
    KNN_PREPROCESS_TAGS,
    get_classifier,
    is_knn_preprocess,
    recognize_digits,
    recognize_digits_prediction,
)
from kNN.digital.paths import dataset_dir, digital_data_dir, model_path

__all__ = [
    "DEFAULT_DIGIT_COUNT",
    "DEFAULT_X0",
    "KNN_PREPROCESS_TAGS",
    "DigitClassifier",
    "DigitPrediction",
    "augment_glyph",
    "build_training_matrices",
    "dataset_dir",
    "digital_data_dir",
    "estimate_digit_count",
    "extract_labeled_glyphs",
    "get_classifier",
    "glyph_to_feature",
    "is_knn_preprocess",
    "model_path",
    "parse_digit_count",
    "recognize_digits",
    "recognize_digits_prediction",
    "render_synthetic_digit",
    "save_dataset_meta",
    "segment_digit_boxes",
]
