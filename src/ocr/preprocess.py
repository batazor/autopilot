from __future__ import annotations

from typing import TYPE_CHECKING

import cv2  # type: ignore[import-untyped]

if TYPE_CHECKING:
    import numpy as np

    from layout.types import Region


def crop_region(image: np.ndarray, region: Region) -> np.ndarray:
    return image[region.y : region.y + region.h, region.x : region.x + region.w]


def enhance_for_ocr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    return cv2.resize(binary, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)


# Region ``type:`` values that imply short, single-line, digit-heavy content
# (countdown timers ``HH:MM:SS``, stat cells like ``12,345`` / ``1.2M``).
# These regions get an automatic ``preprocess: fast_line`` default. Local
# Tesseract maps that to single-line page segmentation, which is a better fit
# for tiny timer/stat crops. The set is small on purpose: ``string`` content
# (multi-word labels, varying layout) still needs block-style segmentation.
_FAST_LINE_TYPE_HINTS: frozenset[str] = frozenset({"time", "int", "integer"})


def resolve_preprocess(
    explicit: str | None,
    type_hint: str | None,
) -> str | None:
    """Resolve the OCR preprocess pipeline tag for one region call site.

    Resolution order (first non-empty wins):

    1. ``explicit`` — the value set on the rule/step or area.json region by
       the operator (caller picks which one wins, usually rule > region).
    2. Auto-derivation from ``type_hint`` — ``time`` / ``int`` / ``integer``
       default to ``"fast_line"`` (single-line OCR mode). Other types
       (``string``, missing) fall through to ``None``.

    To opt OUT of the ``fast_line`` default on a ``type: time`` region, set
    ``preprocess`` to any explicit value (``enhance``, etc.). To opt INTO
    ``fast_line`` on a ``type: string`` region, set ``preprocess: fast_line``
    explicitly.

    ``None`` return means "send no preprocess key" — the backend runs the
    historical full-pipeline path on the raw crop.
    """
    if explicit:
        v = str(explicit).strip().lower()
        if v:
            return v
    if type_hint:
        v = str(type_hint).strip().lower()
        if v in _FAST_LINE_TYPE_HINTS:
            return "fast_line"
    return None
