from __future__ import annotations

from typing import TYPE_CHECKING

import cv2  # type: ignore[import-untyped]

if TYPE_CHECKING:
    import numpy as np

    from layout.types import Region

# Upscale factor for CLAHE + Otsu pipelines
# (``enhance`` / ``enhance_line`` / ``title_line`` / ``digits``).
_OCR_BINARY_UPSCALE = 3.0

# Tesseract whitelist for digit-only regions (``digits`` preprocess / ``type: int``).
DIGITS_CHAR_WHITELIST = "0123456789"

# Tesseract whitelist for item word regions. Spaces are restored by TSV line
# joining and final cleanup; keeping them out of the whitelist avoids shell/CLI
# ambiguity around trailing whitespace in the config value.
WORD_CHAR_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


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


# White-text threshold for ``bar_timer`` (timer glyphs are pure white; the
# progress-bar fill behind them is saturated green/blue). Otsu picks a split
# inside the busy bar gradient and mangles glyph edges ("4d" → "Ad"), so a
# fixed cut works better here.
_BAR_TIMER_WHITE_THRESHOLD = 160


def bar_timer_for_ocr(image: np.ndarray) -> np.ndarray:
    """Timer text drawn over a colored progress bar ("4d 11:59:43").

    Keep only the white glyphs (fixed threshold), render them black-on-white,
    and upscale — CLAHE/Otsu pipelines misread the day prefix on the busy
    bar background.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    mask = (gray > _BAR_TIMER_WHITE_THRESHOLD).astype("uint8") * 255
    inverted = 255 - mask
    return cv2.resize(
        inverted,
        None,
        fx=_OCR_BINARY_UPSCALE,
        fy=_OCR_BINARY_UPSCALE,
        interpolation=cv2.INTER_CUBIC,
    )


# Timers: Tesseract single-line (PSM 7), no whitelist — colons must survive.
_FAST_LINE_TYPE_HINTS: frozenset[str] = frozenset({"time"})
# Integer stat cells (player id, power, server id): PSM 7 + digit whitelist so
# Tesseract can't emit a stray glyph (``&``, ``§``…) for an ambiguous digit and
# silently shorten the number once non-digits are stripped downstream.
_FAST_DIGITS_TYPE_HINTS: frozenset[str] = frozenset({"int", "integer"})


def resolve_preprocess(
    explicit: str | None,
    type_hint: str | None,
) -> str | None:
    """Resolve the OCR preprocess pipeline tag for one region call site.

    Resolution order (first non-empty wins):

    1. ``explicit`` — the value set on the rule/step or area.json region by
       the operator (caller picks which one wins, usually rule > region).
    2. Auto-derivation — ``time`` → ``fast_line``; ``int`` / ``integer`` →
       ``fast_digits``. Other types → ``None``.
    3. ``None`` — raw crop, Tesseract block mode (PSM 6).

    ``fast_line`` / ``fast_digits`` are both PSM 7 on the raw crop; only
    ``fast_digits`` adds the digit whitelist. ``word_line`` is raw PSM 7 with an
    English-letter whitelist plus cleanup. ``enhance`` / ``enhance_line`` /
    ``title_line`` / ``digits`` keep Tesseract pipelines with binarization.
    """
    if explicit:
        v = str(explicit).strip().lower()
        if v:
            return v
    if type_hint:
        v = str(type_hint).strip().lower()
        if v in _FAST_DIGITS_TYPE_HINTS:
            return "fast_digits"
        if v in _FAST_LINE_TYPE_HINTS:
            return "fast_line"
    return None


def parse_digit_count(raw: object) -> int | None:
    """``None`` / ``auto`` -> auto width; positive int -> fixed width."""
    if raw is None:
        return None
    if isinstance(raw, str):
        tag = raw.strip().lower()
        if tag in ("", "auto", "none"):
            return None
        try:
            n = int(tag)
        except ValueError:
            return None
        return n if n > 0 else None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None
