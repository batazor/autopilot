"""Captcha solver using ddddocr (same library as the reference redeem_code.py)."""
from __future__ import annotations

import base64
import logging

logger = logging.getLogger(__name__)

_ocr: object = None


def _get_ocr() -> object:
    global _ocr  # noqa: PLW0603
    if _ocr is None:
        import ddddocr  # type: ignore[import-untyped]
        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def solve_captcha(img_b64: str) -> str:
    """Decode base64 captcha image and return the text (uppercased)."""

    # Strip data-URL prefix if present: "data:image/png;base64,..."
    if "," in img_b64:
        img_b64 = img_b64.split(",", 1)[1]

    img_bytes = base64.b64decode(img_b64)
    ocr = _get_ocr()
    result: str = ocr.classification(img_bytes)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    text = result.strip().upper()
    logger.debug("Captcha solved: %r", text)
    return text
