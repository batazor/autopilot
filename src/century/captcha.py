"""Captcha solver using ddddocr (same library as the reference redeem_code.py)."""
from __future__ import annotations

import base64
import logging
from typing import Any

logger = logging.getLogger(__name__)

_ocr: object = None
_slide_ocr: object = None


def _get_ocr() -> object:
    global _ocr
    if _ocr is None:
        import ddddocr  # type: ignore[import-untyped]

        _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def _get_slide_ocr() -> object:
    global _slide_ocr
    if _slide_ocr is None:
        import ddddocr  # type: ignore[import-untyped]

        _slide_ocr = ddddocr.DdddOcr(det=False, ocr=False, show_ad=False)
    return _slide_ocr


def _image_bytes(img: str | bytes) -> bytes:
    if isinstance(img, bytes):
        return img
    # Strip data-URL prefix if present: "data:image/png;base64,..."
    if "," in img:
        img = img.split(",", 1)[1]
    return base64.b64decode(img)


def solve_captcha(img_b64: str) -> str:
    """Decode base64 captcha image and return the text (uppercased)."""

    img_bytes = _image_bytes(img_b64)
    ocr = _get_ocr()
    result: str = ocr.classification(img_bytes)  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    text = result.strip().upper()
    logger.debug("Captcha solved: %r", text)
    return text


def solve_slider_match(
    target_img: str | bytes,
    background_img: str | bytes,
    *,
    simple_target: bool = False,
) -> dict[str, Any]:
    """Locate a slider puzzle target using ddddocr ``slide_match``."""
    slide = _get_slide_ocr()
    result: dict[str, Any] = slide.slide_match(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        _image_bytes(target_img),
        _image_bytes(background_img),
        simple_target=simple_target,
    )
    return result


def solve_slider_comparison(
    bg_with_gap: str | bytes,
    full_bg: str | bytes,
) -> dict[str, Any]:
    """Locate a slider gap by comparing the gapped and full background images."""
    slide = _get_slide_ocr()
    result: dict[str, Any] = slide.slide_comparison(  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        _image_bytes(bg_with_gap),
        _image_bytes(full_bg),
    )
    return result
