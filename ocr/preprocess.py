from __future__ import annotations

import cv2  # type: ignore[import-untyped]
import numpy as np

from layout.types import Region


def crop_region(image: np.ndarray, region: Region) -> np.ndarray:
    return image[region.y : region.y + region.h, region.x : region.x + region.w]


def enhance_for_ocr(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    _, binary = cv2.threshold(enhanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    upscaled = cv2.resize(binary, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)
    return upscaled
