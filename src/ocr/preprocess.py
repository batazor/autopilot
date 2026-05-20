from __future__ import annotations

from typing import TYPE_CHECKING

import cv2  # type: ignore[import-untyped]

if TYPE_CHECKING:
    import numpy as np

    from layout.types import Region

# Upscale factor for CLAHE + Otsu pipelines (``enhance`` / ``digits``).
_OCR_BINARY_UPSCALE = 3.0

# Tesseract whitelist for digit-only regions (``digits`` preprocess / ``type: int``).
DIGITS_CHAR_WHITELIST = "0123456789"


def crop_region(image: np.ndarray, region: Region) -> np.ndarray:
    return image[region.y : region.y + region.h, region.x : region.x + region.w]


def binary_tile_for_ocr(
    image: np.ndarray,
    *,
    upscale: float = _OCR_BINARY_UPSCALE,
) -> np.ndarray:
    """CLAHE → Otsu → upscale for small UI text (player ids, timers, stats)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return cv2.resize(
        binary,
        None,
        fx=upscale,
        fy=upscale,
        interpolation=cv2.INTER_LINEAR,
    )


def enhance_for_ocr(image: np.ndarray) -> np.ndarray:
    return binary_tile_for_ocr(image)


def digits_for_ocr(image: np.ndarray) -> np.ndarray:
    """Same binarization as ``enhance``; Tesseract uses PSM 8 + digit whitelist."""
    return binary_tile_for_ocr(image)


# Timers: Tesseract single-line (PSM 7).
_FAST_LINE_TYPE_HINTS: frozenset[str] = frozenset({"time"})

# Player ids / integer stat cells: ``kNN/digital`` (``cv2.ml.KNearest``).
_KNN_TYPE_HINTS: frozenset[str] = frozenset({"int", "integer"})


def resolve_preprocess(
    explicit: str | None,
    type_hint: str | None,
) -> str | None:
    """Resolve the OCR preprocess pipeline tag for one region call site.

    Resolution order (first non-empty wins):

    1. ``explicit`` — the value set on the rule/step or area.json region by
       the operator (caller picks which one wins, usually rule > region).
    2. Auto-derivation — ``time`` → ``fast_line``; ``int`` / ``integer`` → ``knn``
       (``src/kNN/digital``). Other types → ``None``.
    3. ``None`` — raw crop, Tesseract block mode (PSM 6).

    ``preprocess: knn`` / ``digital`` force the kNN path; ``digits`` / ``enhance`` keep
    Tesseract pipelines.
    """
    if explicit:
        v = str(explicit).strip().lower()
        if v:
            return v
    if type_hint:
        v = str(type_hint).strip().lower()
        if v in _FAST_LINE_TYPE_HINTS:
            return "fast_line"
        if v in _KNN_TYPE_HINTS:
            return "knn"
    return None
